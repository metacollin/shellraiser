#!/usr/bin/env python3
"""shellraiser – Bash-to-C transpiler.

Parses a practical subset of Bash and emits C source code that links
against the bash_runtime library to produce a native binary.

Usage:
    shellraiser script.sh                   # transpile + compile → ./script
    shellraiser script.sh -o mybin          # custom output name
    shellraiser script.sh --emit-only       # print C to stdout, no compile
    shellraiser script.sh --save-source     # keep the .c file
    shellraiser script.sh --compiler clang  # use a specific compiler
    shellraiser script.sh --cflags="-O2 -g" # custom compiler flags
"""

import sys
import os
import re
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Union

# ====================================================================
#  Token types
# ====================================================================

class TT(Enum):
    WORD       = auto()
    NEWLINE    = auto()
    SEMI       = auto()
    PIPE       = auto()
    AND        = auto()  # &&
    OR         = auto()  # ||
    AMP        = auto()  # & (background – partially supported)
    LPAREN     = auto()
    RPAREN     = auto()
    LBRACE     = auto()  # {
    RBRACE     = auto()  # }
    BANG       = auto()  # !
    DSEMI      = auto()  # ;;
    REDIR_OUT  = auto()  # >
    REDIR_APP  = auto()  # >>
    REDIR_IN   = auto()  # <
    REDIR_ERR  = auto()  # 2>
    REDIR_ERRA = auto()  # 2>>
    REDIR_FD   = auto()  # 2>&1, >&2, 1>&2 (fd duplication)
    # keywords (context-sensitive – treated as WORD in arg position)
    IF         = auto()
    THEN       = auto()
    ELIF       = auto()
    ELSE       = auto()
    FI         = auto()
    WHILE      = auto()
    UNTIL      = auto()
    FOR        = auto()
    DO         = auto()
    DONE       = auto()
    CASE       = auto()
    ESAC       = auto()
    IN         = auto()
    FUNCTION   = auto()
    EOF        = auto()

KEYWORDS = {
    'if': TT.IF, 'then': TT.THEN, 'elif': TT.ELIF, 'else': TT.ELSE,
    'fi': TT.FI, 'while': TT.WHILE, 'until': TT.UNTIL, 'for': TT.FOR,
    'do': TT.DO, 'done': TT.DONE, 'case': TT.CASE, 'esac': TT.ESAC,
    'in': TT.IN, 'function': TT.FUNCTION,
    '{': TT.LBRACE, '}': TT.RBRACE, '!': TT.BANG,
}

# ====================================================================
#  Word parts – describe the pieces inside a single bash "word"
# ====================================================================

@dataclass
class LiteralPart:
    text: str

@dataclass
class SingleQuotedPart:
    text: str

@dataclass
class VarPart:
    name: str          # variable name or special char: ? # @ * 0-9 etc.
    braced: bool = False

@dataclass
class CmdSubstPart:
    command: str       # raw command string inside $( )

@dataclass
class ArithPart:
    expr: str          # expression inside $(( ))

WordPart = Union[LiteralPart, SingleQuotedPart, VarPart, CmdSubstPart, ArithPart]

@dataclass
class Word:
    parts: List[WordPart] = field(default_factory=list)
    quoted: bool = False        # was in double quotes (suppress glob/split)
    has_glob: bool = False      # contains unquoted glob chars

    @property
    def is_simple_literal(self):
        return (len(self.parts) == 1
                and isinstance(self.parts[0], (LiteralPart, SingleQuotedPart)))

    @property
    def literal_value(self):
        if self.is_simple_literal:
            p = self.parts[0]
            return p.text
        return None

# ====================================================================
#  AST nodes
# ====================================================================

@dataclass
class Redirection:
    op: str             # '>' '>>' '<' '2>' '2>>'
    target: Word

@dataclass
class SimpleCommand:
    args: List[Word] = field(default_factory=list)
    redirections: List[Redirection] = field(default_factory=list)
    assignments: List[Tuple[str, Word]] = field(default_factory=list)
    background: bool = False

@dataclass
class Pipeline:
    commands: list       # List[Command]
    negated: bool = False
    background: bool = False

@dataclass
class AndOrList:
    first: 'Pipeline'
    rest: List[Tuple[str, 'Pipeline']] = field(default_factory=list)  # (op, pipeline)

@dataclass
class IfNode:
    condition: List['Statement']
    then_body: List['Statement']
    elifs: List[Tuple[List['Statement'], List['Statement']]] = field(default_factory=list)
    else_body: Optional[List['Statement']] = None

@dataclass
class WhileNode:
    condition: List['Statement']
    body: List['Statement']
    until: bool = False    # True for 'until' loops

@dataclass
class ForNode:
    var: str
    items: Optional[List[Word]]  # None means iterate over "$@"
    body: List['Statement'] = field(default_factory=list)

@dataclass
class CForNode:
    init: str
    cond: str
    step: str
    body: List['Statement'] = field(default_factory=list)

@dataclass
class CaseItem:
    patterns: List[Word]
    body: List['Statement']

@dataclass
class CaseNode:
    word: Word
    items: List[CaseItem] = field(default_factory=list)

@dataclass
class FunctionDef:
    name: str
    body: List['Statement'] = field(default_factory=list)

@dataclass
class AssignmentNode:
    name: str
    value: Word

@dataclass
class ArrayListAssign:
    """arr=(item1 item2 ...)"""
    name: str
    items: List[Word] = field(default_factory=list)

@dataclass
class ArrayIndexAssign:
    """arr[index]=value"""
    name: str
    index_expr: str   # arithmetic expression for index
    value: Word

@dataclass
class ArrayAppendAssign:
    """arr+=(item1 item2 ...)"""
    name: str
    items: List[Word] = field(default_factory=list)

@dataclass
class SubshellNode:
    """( command_list ) — runs body in a forked child process"""
    body: List['Statement'] = field(default_factory=list)

Statement = Union[AndOrList, IfNode, WhileNode, ForNode, CForNode, CaseNode,
                  FunctionDef, AssignmentNode,
                  ArrayListAssign, ArrayIndexAssign, ArrayAppendAssign,
                  SubshellNode]

# ====================================================================
#  Token
# ====================================================================

@dataclass
class Token:
    type: TT
    value: str = ''
    word: Optional[Word] = None
    line: int = 0

# ====================================================================
#  Tokenizer
# ====================================================================

class Tokenizer:
    def __init__(self, source: str):
        self.src = source
        self.pos = 0
        self.line = 1
        self.tokens: List[Token] = []
        self._tokenize()

    def _ch(self, offset=0):
        p = self.pos + offset
        return self.src[p] if p < len(self.src) else '\0'

    def _advance(self, n=1):
        for _ in range(n):
            if self.pos < len(self.src):
                if self.src[self.pos] == '\n':
                    self.line += 1
                self.pos += 1

    def _at_end(self):
        return self.pos >= len(self.src)

    def _tokenize(self):
        while not self._at_end():
            c = self._ch()

            # skip spaces/tabs (not newlines)
            if c in ' \t':
                self._advance()
                continue

            # comments
            if c == '#':
                while not self._at_end() and self._ch() != '\n':
                    self._advance()
                continue

            # newline
            if c == '\n':
                self.tokens.append(Token(TT.NEWLINE, '\n', line=self.line))
                self._advance()
                continue

            # line continuation
            if c == '\\' and self._ch(1) == '\n':
                self._advance(2)
                continue

            # two-char operators
            if c == '&' and self._ch(1) == '&':
                self.tokens.append(Token(TT.AND, '&&', line=self.line))
                self._advance(2); continue
            if c == '|' and self._ch(1) == '|':
                self.tokens.append(Token(TT.OR, '||', line=self.line))
                self._advance(2); continue
            if c == ';' and self._ch(1) == ';':
                self.tokens.append(Token(TT.DSEMI, ';;', line=self.line))
                self._advance(2); continue
            if c == '>' and self._ch(1) == '>':
                self.tokens.append(Token(TT.REDIR_APP, '>>', line=self.line))
                self._advance(2); continue

            # fd duplication redirects: 2>&1, 2>&-, >&2, 1>&2
            if c == '2' and self._ch(1) == '>' and self._ch(2) == '&':
                self._advance(3)  # consume 2>&
                fd = []
                while not self._at_end() and (self._ch().isdigit() or self._ch() == '-'):
                    fd.append(self._ch())
                    self._advance()
                self.tokens.append(Token(TT.REDIR_FD, f'2>&{"".join(fd)}', line=self.line))
                continue
            if c == '>' and self._ch(1) == '&' and (self._ch(2).isdigit() or self._ch(2) == '-'):
                self._advance(2)  # consume >&
                fd = []
                while not self._at_end() and (self._ch().isdigit() or self._ch() == '-'):
                    fd.append(self._ch())
                    self._advance()
                self.tokens.append(Token(TT.REDIR_FD, f'>&{"".join(fd)}', line=self.line))
                continue

            # 2> and 2>>
            if c == '2' and self._ch(1) == '>' and self._ch(2) == '>':
                self.tokens.append(Token(TT.REDIR_ERRA, '2>>', line=self.line))
                self._advance(3); continue
            if c == '2' and self._ch(1) == '>':
                self.tokens.append(Token(TT.REDIR_ERR, '2>', line=self.line))
                self._advance(2); continue

            # single-char operators
            if c == '|':
                self.tokens.append(Token(TT.PIPE, '|', line=self.line))
                self._advance(); continue
            if c == '&':
                self.tokens.append(Token(TT.AMP, '&', line=self.line))
                self._advance(); continue
            if c == ';':
                self.tokens.append(Token(TT.SEMI, ';', line=self.line))
                self._advance(); continue
            if c == '(':
                self.tokens.append(Token(TT.LPAREN, '(', line=self.line))
                self._advance(); continue
            if c == ')':
                self.tokens.append(Token(TT.RPAREN, ')', line=self.line))
                self._advance(); continue
            if c == '>':
                self.tokens.append(Token(TT.REDIR_OUT, '>', line=self.line))
                self._advance(); continue
            if c == '<':
                self.tokens.append(Token(TT.REDIR_IN, '<', line=self.line))
                self._advance(); continue

            # word (includes quoted strings, variable expansions, etc.)
            # Note: { } ! are reserved words in bash, not operators.
            # They are read as part of words and recognized as keywords
            # only when they form a complete standalone word.
            word = self._read_word()
            if word:
                raw = self._word_raw(word)
                # check for keyword
                if word.is_simple_literal and raw in KEYWORDS:
                    self.tokens.append(Token(KEYWORDS[raw], raw, word=word, line=self.line))
                else:
                    self.tokens.append(Token(TT.WORD, raw, word=word, line=self.line))

        self.tokens.append(Token(TT.EOF, '', line=self.line))

    def _last_is_word(self):
        """Check if the previous meaningful token is a word (for keyword disambiguation)."""
        for t in reversed(self.tokens):
            if t.type in (TT.NEWLINE, TT.SEMI):
                return False
            if t.type == TT.WORD:
                return True
            return False
        return False

    def _word_raw(self, w: Word) -> str:
        """Reconstruct raw text of a word (for keyword matching)."""
        parts = []
        for p in w.parts:
            if isinstance(p, LiteralPart):
                parts.append(p.text)
            elif isinstance(p, SingleQuotedPart):
                parts.append(p.text)
            elif isinstance(p, VarPart):
                parts.append(f'${p.name}')
            elif isinstance(p, CmdSubstPart):
                parts.append(f'$({p.command})')
            elif isinstance(p, ArithPart):
                parts.append(f'$(({p.expr}))')
        return ''.join(parts)

    def _read_word(self) -> Optional[Word]:
        """Read a complete word with all its parts."""
        parts: List[WordPart] = []
        has_glob = False
        started = False
        lit_buf: List[str] = []  # accumulate consecutive literal chars

        def flush_lit():
            if lit_buf:
                parts.append(LiteralPart(''.join(lit_buf)))
                lit_buf.clear()

        while not self._at_end():
            c = self._ch()

            # word terminators (operators and metacharacters)
            # Note: { } ! are NOT terminators — they're word chars / reserved words
            if c in ' \t\n;|&()':
                break

            started = True

            if c == '<' or c == '>':
                break

            # backslash escape (outside quotes)
            if c == '\\' and self._ch(1) != '\n':
                self._advance()
                if not self._at_end():
                    lit_buf.append(self._ch())
                    self._advance()
                continue

            if c == '\\' and self._ch(1) == '\n':
                self._advance(2)
                continue

            # single quotes
            if c == "'":
                flush_lit()
                self._advance()
                text = []
                while not self._at_end() and self._ch() != "'":
                    text.append(self._ch())
                    self._advance()
                if not self._at_end():
                    self._advance()  # closing quote
                parts.append(SingleQuotedPart(''.join(text)))
                continue

            # double quotes
            if c == '"':
                flush_lit()
                self._advance()
                parts.extend(self._read_double_quoted())
                continue

            # $'...' ANSI-C quoting
            if c == '$' and self._ch(1) == "'":
                flush_lit()
                self._advance(2)  # skip $'
                text = []
                while not self._at_end() and self._ch() != "'":
                    ch = self._ch()
                    if ch == '\\' and not self._at_end():
                        self._advance()
                        esc = self._ch()
                        if esc == 'n': text.append('\n')
                        elif esc == 't': text.append('\t')
                        elif esc == 'r': text.append('\r')
                        elif esc == 'a': text.append('\a')
                        elif esc == 'b': text.append('\b')
                        elif esc == 'f': text.append('\f')
                        elif esc == 'v': text.append('\v')
                        elif esc == '\\': text.append('\\')
                        elif esc == "'": text.append("'")
                        elif esc == '"': text.append('"')
                        elif esc == '0':
                            # octal: \0NNN
                            val = 0
                            for _ in range(3):
                                if not self._at_end() and '0' <= self._ch(1) <= '7':
                                    self._advance()
                                    val = val * 8 + ord(self._ch()) - ord('0')
                                else:
                                    break
                            text.append(chr(val) if val else '\0')
                        elif esc == 'x':
                            # hex: \xNN
                            val = 0
                            for _ in range(2):
                                nc = self._ch(1) if not self._at_end() else '\0'
                                if nc.isdigit() or nc.lower() in 'abcdef':
                                    self._advance()
                                    val = val * 16 + int(self._ch(), 16)
                                else:
                                    break
                            text.append(chr(val))
                        else:
                            text.append('\\')
                            text.append(esc)
                        self._advance()
                    else:
                        text.append(ch)
                        self._advance()
                if not self._at_end():
                    self._advance()  # closing '
                # Treat as a literal (escapes already resolved)
                parts.append(LiteralPart(''.join(text)))
                continue

            # $((expr))
            if c == '$' and self._ch(1) == '(' and self._ch(2) == '(':
                flush_lit()
                self._advance(3)
                expr = self._read_balanced(')', double=True)
                parts.append(ArithPart(expr))
                continue

            # $(command)
            if c == '$' and self._ch(1) == '(':
                flush_lit()
                self._advance(2)
                cmd = self._read_balanced(')')
                parts.append(CmdSubstPart(cmd))
                continue

            # ${var}
            if c == '$' and self._ch(1) == '{':
                flush_lit()
                self._advance(2)
                name = []
                while not self._at_end() and self._ch() != '}':
                    name.append(self._ch())
                    self._advance()
                if not self._at_end():
                    self._advance()  # closing }
                parts.append(VarPart(''.join(name), braced=True))
                continue

            # $var or $special
            if c == '$':
                flush_lit()
                self._advance()
                if self._at_end():
                    lit_buf.append('$')
                    continue
                nc = self._ch()
                if nc in '?#@*$!-0123456789':
                    parts.append(VarPart(nc))
                    self._advance()
                elif nc.isalpha() or nc == '_':
                    name = []
                    while not self._at_end() and (self._ch().isalnum() or self._ch() == '_'):
                        name.append(self._ch())
                        self._advance()
                    parts.append(VarPart(''.join(name)))
                else:
                    lit_buf.append('$')
                continue

            # backtick command substitution
            if c == '`':
                flush_lit()
                self._advance()
                cmd = []
                while not self._at_end() and self._ch() != '`':
                    if self._ch() == '\\':
                        self._advance()
                        if not self._at_end():
                            cmd.append(self._ch())
                            self._advance()
                    else:
                        cmd.append(self._ch())
                        self._advance()
                if not self._at_end():
                    self._advance()
                parts.append(CmdSubstPart(''.join(cmd)))
                continue

            # glob characters
            if c in '*?[':
                has_glob = True

            # regular character
            lit_buf.append(c)
            self._advance()

        flush_lit()

        if not parts:
            return None

        w = Word(parts=parts, has_glob=has_glob)
        return w

    def _read_double_quoted(self) -> List[WordPart]:
        """Read content inside double quotes until closing \"."""
        parts: List[WordPart] = []
        buf = []

        def flush_buf():
            if buf:
                parts.append(LiteralPart(''.join(buf)))
                buf.clear()

        while not self._at_end() and self._ch() != '"':
            c = self._ch()

            if c == '\\':
                nc = self._ch(1)
                if nc in '"\\$`\n':
                    self._advance()
                    if nc == '\n':
                        self._advance()
                        continue
                    buf.append(nc)
                    self._advance()
                else:
                    buf.append(c)
                    self._advance()
                continue

            if c == '$' and self._ch(1) == '(' and self._ch(2) == '(':
                flush_buf()
                self._advance(3)
                expr = self._read_balanced(')', double=True)
                parts.append(ArithPart(expr))
                continue

            if c == '$' and self._ch(1) == '(':
                flush_buf()
                self._advance(2)
                cmd = self._read_balanced(')')
                parts.append(CmdSubstPart(cmd))
                continue

            if c == '$' and self._ch(1) == '{':
                flush_buf()
                self._advance(2)
                name = []
                while not self._at_end() and self._ch() != '}':
                    name.append(self._ch())
                    self._advance()
                if not self._at_end():
                    self._advance()
                parts.append(VarPart(''.join(name), braced=True))
                continue

            if c == '$':
                self._advance()
                if self._at_end() or self._ch() == '"':
                    buf.append('$')
                    continue
                nc = self._ch()
                if nc in '?#@*$!-0123456789':
                    flush_buf()
                    parts.append(VarPart(nc))
                    self._advance()
                elif nc.isalpha() or nc == '_':
                    flush_buf()
                    name = []
                    while not self._at_end() and (self._ch().isalnum() or self._ch() == '_'):
                        name.append(self._ch())
                        self._advance()
                    parts.append(VarPart(''.join(name)))
                else:
                    buf.append('$')
                continue

            if c == '`':
                flush_buf()
                self._advance()
                cmd = []
                while not self._at_end() and self._ch() != '`':
                    cmd.append(self._ch())
                    self._advance()
                if not self._at_end():
                    self._advance()
                parts.append(CmdSubstPart(''.join(cmd)))
                continue

            buf.append(c)
            self._advance()

        if not self._at_end():
            self._advance()  # closing "

        flush_buf()
        # Mark as quoted
        for p in parts:
            if isinstance(p, LiteralPart):
                pass  # keep as-is; quoting is handled at Word level
        # We return parts; caller wraps in Word with quoted=True
        return parts

    def _read_balanced(self, close_char: str, double: bool = False) -> str:
        """Read until balanced closing paren, handling nesting."""
        depth = 1
        result = []
        close_count = 1 if not double else 2

        while not self._at_end():
            c = self._ch()
            if c == '(':
                depth += 1
                result.append(c)
                self._advance()
            elif c == ')':
                depth -= 1
                if depth == 0:
                    self._advance()
                    if double:
                        # need second )
                        if not self._at_end() and self._ch() == ')':
                            self._advance()
                        break
                    else:
                        break
                result.append(c)
                self._advance()
            elif c == "'" :
                result.append(c)
                self._advance()
                while not self._at_end() and self._ch() != "'":
                    result.append(self._ch())
                    self._advance()
                if not self._at_end():
                    result.append(self._ch())
                    self._advance()
            elif c == '"':
                result.append(c)
                self._advance()
                while not self._at_end() and self._ch() != '"':
                    if self._ch() == '\\':
                        result.append(self._ch())
                        self._advance()
                    if not self._at_end():
                        result.append(self._ch())
                        self._advance()
                if not self._at_end():
                    result.append(self._ch())
                    self._advance()
            else:
                result.append(c)
                self._advance()
        return ''.join(result)


# ====================================================================
#  Parser
# ====================================================================

class ParseError(Exception):
    def __init__(self, msg, line=0):
        super().__init__(f"line {line}: {msg}")
        self.line = line

class Parser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0

    def _cur(self) -> Token:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else Token(TT.EOF)

    def _peek(self, offset=0) -> Token:
        p = self.pos + offset
        return self.tokens[p] if p < len(self.tokens) else Token(TT.EOF)

    def _advance(self) -> Token:
        t = self._cur()
        if self.pos < len(self.tokens):
            self.pos += 1
        return t

    def _expect(self, tt: TT, value=None) -> Token:
        t = self._cur()
        if t.type != tt:
            raise ParseError(f"expected {tt.name} but got {t.type.name} '{t.value}'", t.line)
        if value and t.value != value:
            raise ParseError(f"expected '{value}' but got '{t.value}'", t.line)
        return self._advance()

    def _skip_newlines(self):
        while self._cur().type in (TT.NEWLINE, TT.SEMI):
            self._advance()

    def _at_separator(self):
        return self._cur().type in (TT.NEWLINE, TT.SEMI, TT.EOF)

    def _is_redirect(self):
        return self._cur().type in (TT.REDIR_OUT, TT.REDIR_APP, TT.REDIR_IN,
                                     TT.REDIR_ERR, TT.REDIR_ERRA, TT.REDIR_FD)

    # Keyword/operator tokens that can serve as word arguments in command position.
    # This is safe because their "special" meanings are caught earlier:
    #   LBRACE → _command() routes to _brace_group() before _simple_command()
    #   RBRACE → _compound_list() terminator check exits before _and_or_list()
    #   BANG   → _pipeline() handles negation before _command()
    WORD_LIKE = {TT.WORD, TT.IF, TT.THEN, TT.ELIF, TT.ELSE, TT.FI,
                 TT.WHILE, TT.UNTIL, TT.FOR, TT.DO, TT.DONE,
                 TT.CASE, TT.ESAC, TT.IN, TT.FUNCTION,
                 TT.LBRACE, TT.RBRACE, TT.BANG}

    def _is_word_token(self, t=None):
        """Check if current (or given) token can serve as a word."""
        if t is None:
            t = self._cur()
        return t.type in self.WORD_LIKE

    def _cur_as_word(self) -> Word:
        """Get current token's word, creating one from value if needed."""
        t = self._cur()
        if t.word:
            return t.word
        return Word(parts=[LiteralPart(t.value)])

    def parse(self) -> List[Statement]:
        stmts = self._compound_list()
        if self._cur().type != TT.EOF:
            pass  # ignore trailing tokens
        return stmts

    def _compound_list(self, terminators=None) -> List[Statement]:
        """Parse a list of statements separated by ;, &, or newlines."""
        if terminators is None:
            terminators = {TT.EOF}
        stmts = []
        self._skip_newlines()
        while self._cur().type not in terminators:
            stmt = self._and_or_list()
            if stmt:
                stmts.append(stmt)
            # & is a command terminator that backgrounds the preceding command
            if self._cur().type == TT.AMP:
                self._advance()
                if stmt:
                    self._mark_background(stmt)
                self._skip_newlines()
            elif self._cur().type in (TT.NEWLINE, TT.SEMI):
                self._skip_newlines()
            elif self._cur().type not in terminators:
                break
        return stmts

    @staticmethod
    def _mark_background(stmt):
        """Mark a statement for background execution."""
        if isinstance(stmt, AndOrList):
            stmt.first.background = True
        elif isinstance(stmt, Pipeline):
            stmt.background = True
        elif isinstance(stmt, SimpleCommand):
            stmt.background = True

    def _and_or_list(self) -> Optional[Statement]:
        """Parse: pipeline ( (&&|||) pipeline )*"""
        pl = self._pipeline()
        if pl is None:
            return None

        rest = []
        while self._cur().type in (TT.AND, TT.OR):
            op = self._advance().value
            self._skip_newlines()
            right = self._pipeline()
            if right is None:
                raise ParseError("expected command after " + op, self._cur().line)
            rest.append((op, right))

        if not rest and len(pl.commands) == 1:
            cmd = pl.commands[0]
            # unwrap single-command non-negated pipeline
            if not pl.negated and isinstance(cmd, (IfNode, WhileNode, ForNode,
                                                     CForNode, CaseNode, FunctionDef,
                                                     ArrayListAssign, ArrayAppendAssign)):
                return cmd
            if not pl.negated and isinstance(cmd, SimpleCommand) and not cmd.args:
                # Don't unwrap if there are inline array inits — codegen needs the full SimpleCommand
                if getattr(cmd, '_inline_array_inits', []):
                    pass  # fall through to AndOrList
                else:
                    # bare assignments / array index assignments
                    stmts = []
                    for name, val in cmd.assignments:
                        stmts.append(AssignmentNode(name=name, value=val))
                    if hasattr(cmd, '_array_idx_assigns'):
                        for arr_name, idx_expr, val_word in cmd._array_idx_assigns:
                            stmts.append(ArrayIndexAssign(name=arr_name, index_expr=idx_expr, value=val_word))
                    if len(stmts) == 1:
                        return stmts[0]
                    if stmts:
                        return stmts[0]  # simplified: return first

        aol = AndOrList(first=pl, rest=rest)
        return aol

    def _pipeline(self) -> Optional[Pipeline]:
        """Parse: [!] command ( | command )*"""
        negated = False
        if self._cur().type == TT.BANG:
            negated = True
            self._advance()
            self._skip_newlines()

        cmd = self._command()
        if cmd is None:
            if negated:
                raise ParseError("expected command after !", self._cur().line)
            return None

        commands = [cmd]
        while self._cur().type == TT.PIPE:
            self._advance()
            self._skip_newlines()
            c = self._command()
            if c is None:
                raise ParseError("expected command after |", self._cur().line)
            commands.append(c)

        return Pipeline(commands=commands, negated=negated)

    def _command(self):
        """Parse a single command (simple or compound)."""
        t = self._cur()

        if t.type == TT.IF:
            return self._if_command()
        if t.type == TT.WHILE:
            return self._while_command()
        if t.type == TT.UNTIL:
            return self._until_command()
        if t.type == TT.FOR:
            return self._for_command()
        if t.type == TT.CASE:
            return self._case_command()
        if t.type == TT.FUNCTION:
            return self._function_def()
        if t.type == TT.LBRACE:
            return self._brace_group()

        # check for function definition: name () { ... }
        # name must be a plain identifier (no = or + signs)
        if (t.type == TT.WORD and
            self._peek(1).type == TT.LPAREN and
            self._peek(2).type == TT.RPAREN and
            re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', t.value)):
            return self._function_def_short()

        # check for array assignments: name=(...) or name+=(...)
        if t.type == TT.WORD and self._peek(1).type == TT.LPAREN:
            raw = t.value
            # arr=( ... )
            m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)=$', raw)
            if m:
                return self._array_list_assign(m.group(1))
            # arr+=( ... )
            m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\+=$', raw)
            if m:
                return self._array_append_assign(m.group(1))

        # subshell: ( command_list )
        if t.type == TT.LPAREN:
            return self._subshell()

        # [[ ... ]] extended test — consume everything up to ]] as a single command
        # This prevents && and || inside [[ ]] from being parsed as command separators
        if t.type == TT.WORD and t.value == '[[':
            return self._dblbracket_command()

        return self._simple_command()

    def _dblbracket_command(self) -> SimpleCommand:
        """Parse: [[ expr ]] — collect all tokens including && || as arguments."""
        args = [self._cur_as_word()]
        self._advance()  # consume [[

        while self._cur().type != TT.EOF:
            t = self._cur()
            # Check for ]] as a WORD
            if t.type == TT.WORD and t.value == ']]':
                args.append(self._cur_as_word())
                self._advance()
                break
            # Collect all token types as arguments (&&, ||, !, etc.)
            if t.type in (TT.AND, TT.OR, TT.BANG):
                args.append(Word(parts=[LiteralPart(t.value)]))
                self._advance()
            elif self._is_word_token():
                args.append(self._cur_as_word())
                self._advance()
            elif self._is_redirect():
                # redirections after ]] are possible but rare; skip for now
                self._advance()
            elif t.type in (TT.NEWLINE, TT.SEMI, TT.EOF):
                break  # ]] was missing — break to avoid infinite loop
            else:
                args.append(Word(parts=[LiteralPart(t.value)]))
                self._advance()

        cmd = SimpleCommand(args=args)
        cmd._array_idx_assigns = []
        cmd._inline_array_inits = []
        return cmd

    def _subshell(self) -> SubshellNode:
        """Parse: ( command_list )"""
        self._expect(TT.LPAREN)
        body = self._compound_list(terminators={TT.RPAREN})
        self._expect(TT.RPAREN)
        return SubshellNode(body=body)

    def _array_list_assign(self, name: str):
        """Parse: name=(item1 item2 ...)"""
        self._advance()  # consume the 'name=' word
        self._expect(TT.LPAREN)
        items = []
        while self._cur().type != TT.RPAREN and self._cur().type != TT.EOF:
            if self._cur().type == TT.NEWLINE:
                self._advance()
                continue
            if self._is_word_token():
                items.append(self._cur_as_word())
                self._advance()
            else:
                break
        self._expect(TT.RPAREN)
        return ArrayListAssign(name=name, items=items)

    def _array_append_assign(self, name: str):
        """Parse: name+=(item1 item2 ...)"""
        self._advance()  # consume the 'name+=' word
        self._expect(TT.LPAREN)
        items = []
        while self._cur().type != TT.RPAREN and self._cur().type != TT.EOF:
            if self._cur().type == TT.NEWLINE:
                self._advance()
                continue
            if self._is_word_token():
                items.append(self._cur_as_word())
                self._advance()
            else:
                break
        self._expect(TT.RPAREN)
        return ArrayAppendAssign(name=name, items=items)

    def _simple_command(self) -> Optional[SimpleCommand]:
        """Parse a simple command with optional assignments and redirections."""
        assignments = []
        array_idx_assigns = []   # (name, index_expr, val_word) tuples
        leading_array_inits = [] # (name, [Word items]) for name=(...) in assignment position
        args = []
        redirections = []

        # leading assignments (only from actual WORD tokens)
        while self._cur().type == TT.WORD:
            w = self._cur().word
            raw = self._cur().value
            # check for assignment: word=value (no spaces around =)
            if '=' in raw and not raw.startswith('='):
                eq_idx = raw.index('=')
                name_part = raw[:eq_idx]

                # check for arr[index]=value
                m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\[(.+)\]$', name_part)
                if m:
                    arr_name = m.group(1)
                    idx_expr = m.group(2)
                    self._advance()
                    val_word = self._extract_value_after_eq(w)
                    array_idx_assigns.append((arr_name, idx_expr, val_word))
                    continue

                if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name_part):
                    # Check if this is name=(...) — array list assignment
                    val_after_eq = raw[eq_idx + 1:]
                    if val_after_eq == '' and self._peek(1).type == TT.LPAREN:
                        self._advance()  # consume the 'name=' word
                        self._advance()  # consume (
                        items = []
                        while self._cur().type != TT.RPAREN and self._cur().type != TT.EOF:
                            if self._cur().type == TT.NEWLINE:
                                self._advance()
                                continue
                            if self._is_word_token():
                                items.append(self._cur_as_word())
                                self._advance()
                            else:
                                break
                        if self._cur().type == TT.RPAREN:
                            self._advance()
                        leading_array_inits.append((name_part, items))
                        continue

                    # it's a plain assignment
                    self._advance()
                    # rebuild word for the value part
                    val_word = self._extract_value_after_eq(w)
                    assignments.append((name_part, val_word))
                    continue
            break

        # command name and arguments – accept keyword tokens as words too
        inline_array_inits = []  # (name, [Word items]) for args like data=("$@")
        while True:
            if self._is_redirect():
                redirections.append(self._read_redirect())
                continue
            if self._is_word_token():
                cur = self._cur()
                # Detect inline array init: name=( ... ) as an argument
                # e.g. local -a data=("$@")
                if (cur.type == TT.WORD and
                    self._peek(1).type == TT.LPAREN and
                    cur.value.endswith('=')):
                    m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)=$', cur.value)
                    if m:
                        arr_name = m.group(1)
                        self._advance()  # consume name=
                        self._advance()  # consume (
                        items = []
                        while self._cur().type != TT.RPAREN and self._cur().type != TT.EOF:
                            if self._cur().type == TT.NEWLINE:
                                self._advance()
                                continue
                            if self._is_word_token():
                                items.append(self._cur_as_word())
                                self._advance()
                            else:
                                break
                        if self._cur().type == TT.RPAREN:
                            self._advance()
                        inline_array_inits.append((arr_name, items))
                        continue
                args.append(self._cur_as_word())
                self._advance()
            else:
                break

        if not assignments and not args and not redirections and not array_idx_assigns and not inline_array_inits and not leading_array_inits:
            return None

        cmd = SimpleCommand(args=args, redirections=redirections, assignments=assignments)
        cmd._array_idx_assigns = array_idx_assigns
        cmd._inline_array_inits = leading_array_inits + inline_array_inits
        return cmd

    def _extract_value_after_eq(self, word: Word) -> Word:
        """Extract value from an assignment word by finding the top-level '='.
        Works correctly with mixed part types (LiteralPart, ArithPart, VarPart, etc.).
        The '=' must be in a LiteralPart since other part types are expansions."""
        new_parts = []
        found_eq = False

        for part in word.parts:
            if found_eq:
                new_parts.append(part)
                continue
            if isinstance(part, LiteralPart):
                eq_pos = part.text.find('=')
                if eq_pos >= 0:
                    found_eq = True
                    remainder = part.text[eq_pos + 1:]
                    if remainder:
                        new_parts.append(LiteralPart(remainder))
            # Non-literal parts before '=' are part of the name/index — skip them

        if not new_parts:
            new_parts = [LiteralPart('')]
        return Word(parts=new_parts, quoted=word.quoted)

    def _rebuild_assignment_value(self, original_word: Word, name: str) -> Word:
        """Extract the value part of an assignment word (after the =)."""
        # We need to split the word parts at the = sign
        new_parts = []
        skip_name = len(name) + 1  # name + '='
        char_count = 0
        found_eq = False

        for part in original_word.parts:
            if isinstance(part, (LiteralPart, SingleQuotedPart)):
                text = part.text
                if not found_eq:
                    if char_count + len(text) > skip_name:
                        # The = is in this part
                        remainder = text[skip_name - char_count:]
                        if remainder:
                            new_parts.append(LiteralPart(remainder) if isinstance(part, LiteralPart) else SingleQuotedPart(remainder))
                        found_eq = True
                    elif char_count + len(text) == skip_name:
                        found_eq = True
                    char_count += len(text)
                else:
                    new_parts.append(part)
            else:
                if not found_eq:
                    # this part is still in the name area?
                    # for VarPart etc in name area, this shouldn't happen for valid assignments
                    found_eq = True  # assume we've passed the =
                    new_parts.append(part)
                else:
                    new_parts.append(part)

        if not new_parts:
            new_parts = [LiteralPart('')]
        return Word(parts=new_parts, quoted=original_word.quoted)

    def _read_redirect(self) -> Redirection:
        t = self._advance()

        # fd duplication (2>&1, >&2, etc.) — self-contained, no filename needed
        if t.type == TT.REDIR_FD:
            return Redirection(op=t.value, target=Word(parts=[LiteralPart('')]))

        op_map = {
            TT.REDIR_OUT: '>', TT.REDIR_APP: '>>',
            TT.REDIR_IN: '<', TT.REDIR_ERR: '2>',
            TT.REDIR_ERRA: '2>>',
        }
        op = op_map.get(t.type, '>')
        if not self._is_word_token():
            raise ParseError(f"expected filename after {op}", self._cur().line)
        target_word = self._cur_as_word()
        self._advance()
        return Redirection(op=op, target=target_word)

    def _if_command(self) -> IfNode:
        self._expect(TT.IF)
        cond = self._compound_list(terminators={TT.THEN})
        self._expect(TT.THEN)
        body = self._compound_list(terminators={TT.ELIF, TT.ELSE, TT.FI})

        elifs = []
        while self._cur().type == TT.ELIF:
            self._advance()
            econd = self._compound_list(terminators={TT.THEN})
            self._expect(TT.THEN)
            ebody = self._compound_list(terminators={TT.ELIF, TT.ELSE, TT.FI})
            elifs.append((econd, ebody))

        else_body = None
        if self._cur().type == TT.ELSE:
            self._advance()
            else_body = self._compound_list(terminators={TT.FI})

        self._expect(TT.FI)
        return IfNode(condition=cond, then_body=body, elifs=elifs, else_body=else_body)

    def _while_command(self) -> WhileNode:
        self._expect(TT.WHILE)
        cond = self._compound_list(terminators={TT.DO})
        self._expect(TT.DO)
        body = self._compound_list(terminators={TT.DONE})
        self._expect(TT.DONE)
        return WhileNode(condition=cond, body=body)

    def _until_command(self) -> WhileNode:
        self._expect(TT.UNTIL)
        cond = self._compound_list(terminators={TT.DO})
        self._expect(TT.DO)
        body = self._compound_list(terminators={TT.DONE})
        self._expect(TT.DONE)
        return WhileNode(condition=cond, body=body, until=True)

    def _for_command(self) -> Union[ForNode, CForNode]:
        self._expect(TT.FOR)

        # C-style for: for (( init; cond; step ))
        if self._cur().type == TT.LPAREN and self._peek(1).type == TT.LPAREN:
            self._advance()  # (
            self._advance()  # (
            # read until ))
            parts = []
            depth = 2
            while self._cur().type != TT.EOF:
                if self._cur().type == TT.RPAREN:
                    depth -= 1
                    if depth <= 0:
                        self._advance()
                        break
                    if depth == 1:
                        # This is the first ) of )) — don't include it
                        self._advance()
                        continue
                    parts.append(self._advance().value)
                elif self._cur().type == TT.LPAREN:
                    depth += 1
                    parts.append(self._advance().value)
                else:
                    parts.append(self._advance().value)

            # parse the three parts separated by ;
            text = ' '.join(parts)
            # Rejoin operators that the tokenizer split apart
            text = re.sub(r'< =', '<=', text)
            text = re.sub(r'> =', '>=', text)
            text = re.sub(r'= =', '==', text)
            text = re.sub(r'! =', '!=', text)
            text = re.sub(r'\+ \+', '++', text)
            text = re.sub(r'- -', '--', text)
            text = re.sub(r'\+ =', '+=', text)
            text = re.sub(r'- =', '-=', text)
            text = re.sub(r'\* =', '*=', text)
            text = re.sub(r'/ =', '/=', text)
            segs = text.split(';')
            init = segs[0].strip() if len(segs) > 0 else ''
            cond = segs[1].strip() if len(segs) > 1 else '1'
            step = segs[2].strip() if len(segs) > 2 else ''

            self._skip_newlines()
            self._expect(TT.DO)
            body = self._compound_list(terminators={TT.DONE})
            self._expect(TT.DONE)
            return CForNode(init=init, cond=cond, step=step, body=body)

        # standard for: for var [in items]; do ... done
        if not self._is_word_token():
            raise ParseError("expected variable name after 'for'", self._cur().line)
        var = self._cur().value
        self._advance()

        items = None
        if self._cur().type == TT.IN:
            self._advance()
            items = []
            while self._is_word_token() and self._cur().type not in (TT.DO, TT.SEMI):
                items.append(self._cur_as_word())
                self._advance()

        self._skip_newlines()
        if self._cur().type == TT.SEMI:
            self._advance()
        self._skip_newlines()
        self._expect(TT.DO)
        body = self._compound_list(terminators={TT.DONE})
        self._expect(TT.DONE)
        return ForNode(var=var, items=items, body=body)

    def _case_command(self) -> CaseNode:
        self._expect(TT.CASE)
        if not self._is_word_token():
            raise ParseError("expected word after 'case'", self._cur().line)
        word = self._cur_as_word()
        self._advance()
        self._skip_newlines()
        self._expect(TT.IN)
        self._skip_newlines()

        items = []
        while self._cur().type != TT.ESAC and self._cur().type != TT.EOF:
            # optional (
            if self._cur().type == TT.LPAREN:
                self._advance()

            # pattern list: pat1 | pat2 )
            patterns = []
            while True:
                if self._is_word_token() or self._cur().type == TT.BANG:
                    patterns.append(self._cur_as_word())
                    self._advance()
                else:
                    break
                if self._cur().type == TT.PIPE:
                    self._advance()
                else:
                    break

            self._expect(TT.RPAREN)
            self._skip_newlines()

            body = self._compound_list(terminators={TT.DSEMI, TT.ESAC})
            items.append(CaseItem(patterns=patterns, body=body))

            if self._cur().type == TT.DSEMI:
                self._advance()
                self._skip_newlines()

        self._expect(TT.ESAC)
        return CaseNode(word=word, items=items)

    def _function_def(self) -> FunctionDef:
        """Parse: function name { ... }"""
        self._expect(TT.FUNCTION)
        if not self._is_word_token():
            raise ParseError("expected function name", self._cur().line)
        name = self._cur().value
        self._advance()
        # optional ()
        if self._cur().type == TT.LPAREN:
            self._advance()
            self._expect(TT.RPAREN)
        self._skip_newlines()
        self._expect(TT.LBRACE)
        body = self._compound_list(terminators={TT.RBRACE})
        self._expect(TT.RBRACE)
        return FunctionDef(name=name, body=body)

    def _function_def_short(self) -> FunctionDef:
        """Parse: name() { ... }"""
        name = self._advance().value
        self._expect(TT.LPAREN)
        self._expect(TT.RPAREN)
        self._skip_newlines()
        self._expect(TT.LBRACE)
        body = self._compound_list(terminators={TT.RBRACE})
        self._expect(TT.RBRACE)
        return FunctionDef(name=name, body=body)

    def _brace_group(self):
        """Parse: { list ; }"""
        self._expect(TT.LBRACE)
        body = self._compound_list(terminators={TT.RBRACE})
        self._expect(TT.RBRACE)
        # Return as a simple pipeline wrapper
        if len(body) == 1:
            return body[0]
        # Wrap in a pseudo-node – we'll generate as a block
        cmd = SimpleCommand()
        cmd._block_body = body
        return cmd


# ====================================================================
#  C Code Generator
# ====================================================================

def c_escape(s: str) -> str:
    """Escape a string for embedding in C source code."""
    return (s.replace('\\', '\\\\')
             .replace('"', '\\"')
             .replace('\n', '\\n')
             .replace('\r', '\\r')
             .replace('\t', '\\t')
             .replace('\0', '\\0'))


class CodeGen:
    def __init__(self):
        self.lines: List[str] = []
        self.indent = 1     # start inside main()
        self.tmp_id = 0
        self.functions: List[FunctionDef] = []
        self.func_names: set = set()
        self.cleanup_stack: List[List[str]] = [[]]  # stack of lists of temps to free

    def _tmp(self, prefix='_t') -> str:
        self.tmp_id += 1
        return f'{prefix}{self.tmp_id}'

    def _emit(self, line: str):
        self.lines.append('    ' * self.indent + line)

    def _emit_raw(self, line: str):
        self.lines.append(line)

    def _push_cleanup(self):
        self.cleanup_stack.append([])

    def _add_cleanup(self, var: str):
        self.cleanup_stack[-1].append(var)

    def _pop_cleanup(self):
        temps = self.cleanup_stack.pop()
        for t in temps:
            self._emit(f'free({t});')

    def _c_func_name(self, bash_name: str) -> str:
        safe = re.sub(r'[^a-zA-Z0-9_]', '_', bash_name)
        return f'func_{safe}'

    # ---------------------------------------------------------------
    #  Top-level generation
    # ---------------------------------------------------------------

    def generate(self, stmts: List[Statement]) -> str:
        # First pass: collect function definitions
        self._collect_functions(stmts)
        remaining = [s for s in stmts if not isinstance(s, FunctionDef)]

        header = [
            '#define _POSIX_C_SOURCE 200809L',
            '#include "bash_runtime.h"',
            '#include <string.h>',
            '',
        ]

        # Forward declarations (not static — needed by function table)
        for fd in self.functions:
            cname = self._c_func_name(fd.name)
            header.append(f'int {cname}(int argc, char **argv);')

        # Function registration table
        if self.functions:
            header.append('')
            header.append(f'static FuncEntry _shellraiser_func_table[] = {{')
            for fd in self.functions:
                cname = self._c_func_name(fd.name)
                header.append(f'    {{ "{c_escape(fd.name)}", {cname} }},')
            header.append('};')
            header.append(f'static int _shellraiser_func_count = {len(self.functions)};')
        else:
            header.append('')
            header.append('static FuncEntry *_shellraiser_func_table = NULL;')
            header.append('static int _shellraiser_func_count = 0;')

        header.append('')
        header.append('int main(int argc, char **argv) {')
        header.append('    rt_init(argc, argv);')
        header.append('    rt_register_functions(_shellraiser_func_table, _shellraiser_func_count);')
        header.append('')

        # --call dispatch: if argv[1] is "--call", route to named function
        header.append('    /* --call dispatch for exported function invocation */')
        header.append('    if (argc >= 3 && strcmp(argv[1], "--call") == 0) {')
        header.append('        const char *fname = argv[2];')
        header.append('        /* shift argv so the function sees its own args */')
        header.append('        int fargc = argc - 2;')
        header.append('        char **fargv = argv + 2;')
        header.append('        int rc = rt_dispatch_call(fname, fargc, fargv);')
        header.append('        rt_cleanup();')
        header.append('        return rc;')
        header.append('    }')
        header.append('')

        self.lines = []
        for stmt in remaining:
            self._push_cleanup()
            self._gen_statement(stmt)
            self._pop_cleanup()

        main_body = self.lines

        footer = [
            '',
            '    rt_cleanup();',
            '    return rt.last_exit;',
            '}',
        ]

        # Generate function bodies
        func_code = []
        for fd in self.functions:
            func_code.append('')
            func_code.extend(self._gen_function(fd))

        return '\n'.join(header + main_body + footer + func_code) + '\n'

    def _collect_functions(self, stmts):
        for s in stmts:
            if isinstance(s, FunctionDef):
                self.functions.append(s)
                self.func_names.add(s.name)

    def _gen_function(self, fd: FunctionDef) -> List[str]:
        cname = self._c_func_name(fd.name)
        lines = [
            f'int {cname}(int argc, char **argv) {{',
            '    rt_push_scope();',
            '    rt_push_args(argc, argv);',
            '',
        ]
        saved = self.lines
        saved_indent = self.indent
        self.lines = []
        self.indent = 1

        for stmt in fd.body:
            self._push_cleanup()
            self._gen_statement(stmt)
            self._emit('if (rt.func_returning) goto _func_end;')
            self._pop_cleanup()

        lines.extend(self.lines)
        lines.append('')
        lines.append('_func_end:')
        lines.append('    rt.func_returning = 0;')
        lines.append('    rt_pop_args();')
        lines.append('    rt_pop_scope();')
        lines.append('    return rt.last_exit;')
        lines.append('}')

        self.lines = saved
        self.indent = saved_indent
        return lines

    # ---------------------------------------------------------------
    #  Statement dispatch
    # ---------------------------------------------------------------

    def _gen_statement(self, stmt):
        if isinstance(stmt, AssignmentNode):
            self._gen_assignment(stmt)
        elif isinstance(stmt, ArrayListAssign):
            self._gen_array_list_assign(stmt)
        elif isinstance(stmt, ArrayIndexAssign):
            self._gen_array_index_assign(stmt)
        elif isinstance(stmt, ArrayAppendAssign):
            self._gen_array_append_assign(stmt)
        elif isinstance(stmt, AndOrList):
            self._gen_andor(stmt)
        elif isinstance(stmt, IfNode):
            self._gen_if(stmt)
        elif isinstance(stmt, WhileNode):
            self._gen_while(stmt)
        elif isinstance(stmt, ForNode):
            self._gen_for(stmt)
        elif isinstance(stmt, CForNode):
            self._gen_cfor(stmt)
        elif isinstance(stmt, CaseNode):
            self._gen_case(stmt)
        elif isinstance(stmt, Pipeline):
            self._gen_pipeline(stmt)
        elif isinstance(stmt, SimpleCommand):
            self._gen_simple_command(stmt)
        elif isinstance(stmt, SubshellNode):
            self._gen_subshell(stmt)
        elif isinstance(stmt, FunctionDef):
            pass  # handled separately

    # ---------------------------------------------------------------
    #  Assignments
    # ---------------------------------------------------------------

    def _gen_assignment(self, node: AssignmentNode):
        val_expr = self._gen_word_expr(node.value)
        needs_free = self._word_needs_free(node.value)
        if needs_free:
            tmp = self._tmp()
            self._emit(f'char *{tmp} = (char *){val_expr};')
            self._emit(f'rt_set_var("{c_escape(node.name)}", {tmp});')
            self._emit(f'free({tmp});')
        else:
            self._emit(f'rt_set_var("{c_escape(node.name)}", {val_expr});')

    # ---------------------------------------------------------------
    #  Array Assignments
    # ---------------------------------------------------------------

    def _gen_array_list_assign(self, node: ArrayListAssign):
        """Generate: arr=(item1 item2 ...)"""
        self._emit('{')
        self.indent += 1
        self._push_cleanup()

        item_exprs = []
        for w in node.items:
            expr = self._gen_word_expr(w)
            if self._word_needs_free(w):
                tmp = self._tmp('_ai')
                self._emit(f'char *{tmp} = (char *){expr};')
                self._add_cleanup(tmp)
                item_exprs.append(tmp)
            else:
                item_exprs.append(f'(char *){expr}')

        arr_var = self._tmp('_al')
        self._emit(f'char *{arr_var}[] = {{{", ".join(item_exprs)}}};')
        self._emit(f'rt_array_set_list("{c_escape(node.name)}", {len(item_exprs)}, {arr_var});')

        self._pop_cleanup()
        self.indent -= 1
        self._emit('}')

    def _gen_array_index_assign(self, node: ArrayIndexAssign):
        """Generate: arr[index]=value"""
        val_expr = self._gen_word_expr(node.value)
        needs_free = self._word_needs_free(node.value)
        idx = c_escape(self._clean_arith_expr(node.index_expr))
        if needs_free:
            tmp = self._tmp()
            self._emit(f'char *{tmp} = (char *){val_expr};')
            self._emit(f'rt_array_set("{c_escape(node.name)}", (int)rt_arith_eval("{idx}"), {tmp});')
            self._emit(f'free({tmp});')
        else:
            self._emit(f'rt_array_set("{c_escape(node.name)}", (int)rt_arith_eval("{idx}"), {val_expr});')

    def _gen_array_append_assign(self, node: ArrayAppendAssign):
        """Generate: arr+=(item1 item2 ...)"""
        for w in node.items:
            expr = self._gen_word_expr(w)
            needs_free = self._word_needs_free(w)
            if needs_free:
                tmp = self._tmp()
                self._emit(f'char *{tmp} = (char *){expr};')
                self._emit(f'rt_array_append("{c_escape(node.name)}", {tmp});')
                self._emit(f'free({tmp});')
            else:
                self._emit(f'rt_array_append("{c_escape(node.name)}", {expr});')

    def _gen_inline_array_inits(self, inits):
        """Generate code for inline array initializations like data=("$@") or sorted=($(cmd))."""
        for arr_name, items in inits:
            # Special case: data=("$@") — copy positional params as separate elements
            if (len(items) == 1 and len(items[0].parts) == 1
                    and isinstance(items[0].parts[0], VarPart)
                    and items[0].parts[0].name in ('@', '*')):
                self._emit('{')
                self.indent += 1
                cnt = self._tmp('_pc')
                self._emit(f'int {cnt} = rt_get_argc();')
                self._emit(f'rt_array_unset("{c_escape(arr_name)}");')
                idx = self._tmp('_pi')
                self._emit(f'for (int {idx} = 1; {idx} <= {cnt}; {idx}++)')
                self.indent += 1
                self._emit(f'rt_array_append("{c_escape(arr_name)}", rt_get_arg({idx}));')
                self.indent -= 1
                self.indent -= 1
                self._emit('}')
                continue

            # Special case: data=("${src[@]}") — copy another array
            if (len(items) == 1 and len(items[0].parts) == 1
                    and isinstance(items[0].parts[0], VarPart)):
                m = re.match(r'^([a-zA-Z_]\w*)\[[@*]\]$', items[0].parts[0].name)
                if m:
                    src_name = m.group(1)
                    self._emit('{')
                    self.indent += 1
                    cnt = self._tmp('_sc')
                    arr = self._tmp('_se')
                    self._emit(f'int {cnt} = 0;')
                    self._emit(f'char **{arr} = rt_array_get_all("{c_escape(src_name)}", &{cnt});')
                    self._emit(f'rt_array_unset("{c_escape(arr_name)}");')
                    idx = self._tmp('_si')
                    self._emit(f'for (int {idx} = 0; {idx} < {cnt}; {idx}++)')
                    self.indent += 1
                    self._emit(f'rt_array_append("{c_escape(arr_name)}", {arr}[{idx}]);')
                    self.indent -= 1
                    self._emit(f'free({arr});')
                    self.indent -= 1
                    self._emit('}')
                    continue

            # Special case: arr=($(cmd)) — single command substitution, split output into elements
            if (len(items) == 1 and len(items[0].parts) == 1
                    and isinstance(items[0].parts[0], CmdSubstPart)):
                cmd_str = items[0].parts[0].command
                self._emit('{')
                self.indent += 1
                cs_expr = self._gen_cmd_subst_expr(cmd_str)
                tmp = self._tmp('_cs')
                self._emit(f'char *{tmp} = {cs_expr};')
                wc = self._tmp('_wc')
                wv = self._tmp('_wv')
                self._emit(f'int {wc} = 0;')
                self._emit(f'char **{wv} = rt_split_words({tmp}, &{wc});')
                self._emit(f'free({tmp});')
                self._emit(f'rt_array_unset("{c_escape(arr_name)}");')
                wi = self._tmp('_wi')
                self._emit(f'for (int {wi} = 0; {wi} < {wc}; {wi}++)')
                self.indent += 1
                self._emit(f'rt_array_append("{c_escape(arr_name)}", {wv}[{wi}]);')
                self.indent -= 1
                self._emit(f'rt_split_free({wv}, {wc});')
                self.indent -= 1
                self._emit('}')
                continue

            # General case: known list of literal items
            self._emit('{')
            self.indent += 1
            self._push_cleanup()
            item_exprs = []
            for w in items:
                expr = self._gen_word_expr(w)
                if self._word_needs_free(w):
                    tmp = self._tmp('_ai')
                    self._emit(f'char *{tmp} = (char *){expr};')
                    self._add_cleanup(tmp)
                    item_exprs.append(tmp)
                else:
                    item_exprs.append(f'(char *){expr}')
            if item_exprs:
                alv = self._tmp('_al')
                self._emit(f'char *{alv}[] = {{{", ".join(item_exprs)}}};')
                self._emit(f'rt_array_set_list("{c_escape(arr_name)}", {len(item_exprs)}, {alv});')
            else:
                self._emit(f'rt_array_set_list("{c_escape(arr_name)}", 0, NULL);')
            self._pop_cleanup()
            self.indent -= 1
            self._emit('}')

    # ---------------------------------------------------------------
    #  And-Or lists
    # ---------------------------------------------------------------

    def _gen_andor(self, node: AndOrList):
        self._gen_pipeline(node.first)
        for op, pl in node.rest:
            if op == '&&':
                self._emit('if (rt.last_exit == 0) {')
            else:
                self._emit('if (rt.last_exit != 0) {')
            self.indent += 1
            self._gen_pipeline(pl)
            self.indent -= 1
            self._emit('}')

    # ---------------------------------------------------------------
    #  Pipeline
    # ---------------------------------------------------------------

    def _gen_pipeline(self, node):
        if isinstance(node, Pipeline):
            if len(node.commands) == 1 and not node.negated:
                # pass background flag to the command
                if node.background and isinstance(node.commands[0], SimpleCommand):
                    node.commands[0].background = True
                self._gen_statement(node.commands[0])
                return
            if len(node.commands) == 1:
                if node.background and isinstance(node.commands[0], SimpleCommand):
                    node.commands[0].background = True
                self._gen_statement(node.commands[0])
                self._emit('rt.last_exit = rt.last_exit ? 0 : 1;')
                return

            # Multi-command pipeline
            self._emit('{')
            self.indent += 1
            self._push_cleanup()

            pipe_var = self._tmp('_pipe')
            self._emit(f'char **{pipe_var}[{len(node.commands) + 1}];')

            for i, cmd in enumerate(node.commands):
                if isinstance(cmd, SimpleCommand):
                    args_var, _ = self._gen_argv_array(cmd.args, prefix=f'_pa{i}')
                    self._emit(f'{pipe_var}[{i}] = {args_var};')
                else:
                    # compound command in pipeline – not fully supported, fallback
                    self._emit(f'{pipe_var}[{i}] = (char *[]){{"echo", "compound-in-pipe-unsupported", NULL}};')

            self._emit(f'{pipe_var}[{len(node.commands)}] = NULL;')
            self._emit(f'rt_exec_pipeline_v({pipe_var}, {len(node.commands)});')

            if node.negated:
                self._emit('rt.last_exit = rt.last_exit ? 0 : 1;')

            self._pop_cleanup()
            self.indent -= 1
            self._emit('}')
        else:
            self._gen_statement(node)

    # ---------------------------------------------------------------
    #  Subshell
    # ---------------------------------------------------------------

    def _gen_subshell(self, node: SubshellNode):
        self._emit('{')
        self.indent += 1
        self._emit('rt_sync_env();')
        pid = self._tmp('_spid')
        self._emit(f'pid_t {pid} = fork();')
        self._emit(f'if ({pid} < 0) {{ perror("fork"); rt.last_exit = 1; }}')
        self._emit(f'else if ({pid} == 0) {{')
        self.indent += 1
        self._emit('/* child – subshell */')
        for s in node.body:
            self._push_cleanup()
            self._gen_statement(s)
            self._pop_cleanup()
        self._emit('_exit(rt.last_exit);')
        self.indent -= 1
        self._emit('} else {')
        self.indent += 1
        self._emit('/* parent – wait for subshell */')
        st = self._tmp('_sst')
        self._emit(f'int {st};')
        self._emit(f'waitpid({pid}, &{st}, 0);')
        self._emit(f'rt.last_exit = WIFEXITED({st}) ? WEXITSTATUS({st}) : 128;')
        self.indent -= 1
        self._emit('}')
        self.indent -= 1
        self._emit('}')

    # ---------------------------------------------------------------
    #  Simple command
    # ---------------------------------------------------------------

    def _gen_simple_command(self, cmd: SimpleCommand):
        if hasattr(cmd, '_block_body'):
            for s in cmd._block_body:
                self._push_cleanup()
                self._gen_statement(s)
                self._pop_cleanup()
            return

        # Handle leading assignments
        for name, val in cmd.assignments:
            val_expr = self._gen_word_expr(val)
            needs_free = self._word_needs_free(val)
            if needs_free:
                tmp = self._tmp()
                self._emit(f'char *{tmp} = (char *){val_expr};')
                self._emit(f'rt_set_var("{c_escape(name)}", {tmp});')
                self._emit(f'free({tmp});')
            else:
                self._emit(f'rt_set_var("{c_escape(name)}", {val_expr});')

        # Handle array index assignments
        if hasattr(cmd, '_array_idx_assigns'):
            for arr_name, idx_expr, val_word in cmd._array_idx_assigns:
                val_expr = self._gen_word_expr(val_word)
                needs_free = self._word_needs_free(val_word)
                idx = c_escape(self._clean_arith_expr(idx_expr))
                if needs_free:
                    tmp = self._tmp()
                    self._emit(f'char *{tmp} = (char *){val_expr};')
                    self._emit(f'rt_array_set("{c_escape(arr_name)}", (int)rt_arith_eval("{idx}"), {tmp});')
                    self._emit(f'free({tmp});')
                else:
                    self._emit(f'rt_array_set("{c_escape(arr_name)}", (int)rt_arith_eval("{idx}"), {val_expr});')

        # Handle inline array inits (from leading position or arg position)
        inline_inits = getattr(cmd, '_inline_array_inits', [])
        if inline_inits:
            self._gen_inline_array_inits(inline_inits)
            if not cmd.args:
                return

        if not cmd.args:
            return
            return

        # Check if first arg is a known function
        first = cmd.args[0]
        if first.is_simple_literal and first.literal_value in self.func_names:
            self._gen_func_call(first.literal_value, cmd.args[1:])
            return

        # Handle continue/break as C statements (not commands)
        if first.is_simple_literal and first.literal_value == 'continue':
            self._emit('continue;')
            return
        if first.is_simple_literal and first.literal_value == 'break':
            self._emit('break;')
            return

        # Handle inline array inits (e.g. local -a data=("$@"))
        inline_inits = getattr(cmd, '_inline_array_inits', [])
        if inline_inits:
            # Emit the regular command (local/declare) for non-array args first
            non_array_args = [a for a in cmd.args
                              if not (a.is_simple_literal and a.literal_value == '-a')]
            if non_array_args and len(non_array_args) > 1:
                self._emit('{')
                self.indent += 1
                self._push_cleanup()
                argv_var, _ = self._gen_argv_array(non_array_args)
                self._emit(f'rt_exec_simple({argv_var});')
                self._pop_cleanup()
                self.indent -= 1
                self._emit('}')

            self._gen_inline_array_inits(inline_inits)
            return

        bg = getattr(cmd, 'background', False)

        self._emit('{')
        self.indent += 1
        self._push_cleanup()

        # Build argv
        argv_var, _ = self._gen_argv_array(cmd.args)

        # Determine redirections
        in_file = 'NULL'
        out_file = 'NULL'
        out_append = '0'
        err_file = 'NULL'
        err_append = '0'
        has_redir = bool(cmd.redirections)

        for r in cmd.redirections:
            # fd duplication: 2>&1, >&2, etc.
            if r.op.startswith(('2>&', '>&', '1>&')):
                has_redir = True
                # Extract target fd
                fd_str = r.op.split('&')[1]
                if r.op.startswith('2>'):
                    # stderr → target fd: pass special "&N" as err_file
                    err_file = f'"&{c_escape(fd_str)}"'
                    err_append = '0'
                else:
                    # stdout → target fd: pass special "&N" as out_file
                    out_file = f'"&{c_escape(fd_str)}"'
                    out_append = '0'
                continue

            target_expr = self._gen_word_expr(r.target)
            target_needs_free = self._word_needs_free(r.target)
            if target_needs_free:
                tmp = self._tmp()
                self._emit(f'char *{tmp} = (char*){target_expr};')
                self._add_cleanup(tmp)
                target_expr = tmp

            if r.op == '<':
                in_file = target_expr
            elif r.op == '>':
                out_file = target_expr
                out_append = '0'
            elif r.op == '>>':
                out_file = target_expr
                out_append = '1'
            elif r.op == '2>':
                err_file = target_expr
                err_append = '0'
            elif r.op == '2>>':
                err_file = target_expr
                err_append = '1'

        if bg:
            if has_redir:
                self._emit(f'rt_exec_background_redir({argv_var}, {in_file}, {out_file}, {out_append}, {err_file}, {err_append});')
            else:
                self._emit(f'rt_exec_background({argv_var});')
        else:
            if has_redir:
                self._emit(f'rt_exec_redir({argv_var}, {in_file}, {out_file}, {out_append}, {err_file}, {err_append});')
            else:
                self._emit(f'rt_exec_simple({argv_var});')

        self._pop_cleanup()
        self.indent -= 1
        self._emit('}')

    def _gen_func_call(self, name: str, arg_words: List[Word]):
        cname = self._c_func_name(name)
        self._emit('{')
        self.indent += 1
        self._push_cleanup()

        # Build argument array: [func_name, arg1, arg2, ..., NULL]
        all_args = [Word(parts=[LiteralPart(name)])] + list(arg_words)
        argv_var, argc_expr = self._gen_argv_array(all_args)
        self._emit(f'{cname}({argc_expr}, {argv_var});')

        self._pop_cleanup()
        self.indent -= 1
        self._emit('}')

    def _gen_argv_array(self, args: List[Word], prefix='_argv') -> tuple:
        """Generate a char*[] from a list of Words.
        Returns (argv_var_name, argc_expr) where argc_expr is either
        a literal number or a C variable name holding the dynamic count."""
        # Check if any arg needs dynamic array expansion
        has_dynamic = False
        for w in args:
            if len(w.parts) == 1 and isinstance(w.parts[0], VarPart):
                vn = w.parts[0].name
                if vn in ('@', '*') or re.match(r'^[a-zA-Z_]\w*\[[@*]\]$', vn):
                    has_dynamic = True
                    break

        if not has_dynamic:
            # Static case: fixed number of args
            var = self._tmp(prefix)
            exprs = []
            for w in args:
                expr = self._gen_word_expr(w)
                if self._word_needs_free(w):
                    tmp = self._tmp('_a')
                    self._emit(f'char *{tmp} = (char *){expr};')
                    self._add_cleanup(tmp)
                    exprs.append(tmp)
                else:
                    exprs.append(f'(char *){expr}')
            args_str = ', '.join(exprs + ['NULL'])
            self._emit(f'char *{var}[] = {{{args_str}}};')
            return (var, str(len(exprs)))

        # Dynamic case: some args expand to multiple entries
        cap = self._tmp('_cap')
        cnt = self._tmp('_cnt')
        var = self._tmp(prefix)
        self._emit(f'int {cap} = {len(args) + 16};')
        self._emit(f'int {cnt} = 0;')
        self._emit(f'char **{var} = (char **)malloc(sizeof(char *) * (size_t){cap});')

        macro_add = self._tmp('_add')
        # Emit a helper macro-like block using a local function-like pattern
        # We'll just inline the grow+add logic
        for w in args:
            if len(w.parts) == 1 and isinstance(w.parts[0], VarPart):
                vn = w.parts[0].name
                # $@ / $* → expand positional params
                if vn in ('@', '*'):
                    pc = self._tmp('_pc')
                    pi = self._tmp('_pi')
                    self._emit(f'{{ int {pc} = rt_get_argc();')
                    self._emit(f'  for (int {pi} = 1; {pi} <= {pc}; {pi}++) {{')
                    self._emit(f'    if ({cnt} + 1 >= {cap}) {{ {cap} *= 2; {var} = (char **)realloc({var}, sizeof(char *) * (size_t){cap}); }}')
                    self._emit(f'    {var}[{cnt}++] = (char *)rt_get_arg({pi});')
                    self._emit(f'  }} }}')
                    continue
                # ${arr[@]} / ${arr[*]} → expand array elements
                m = re.match(r'^([a-zA-Z_]\w*)\[[@*]\]$', vn)
                if m:
                    aname = m.group(1)
                    ac = self._tmp('_ac')
                    ae = self._tmp('_ae')
                    ai = self._tmp('_ai')
                    self._emit(f'{{ int {ac} = 0;')
                    self._emit(f'  char **{ae} = rt_array_get_all("{c_escape(aname)}", &{ac});')
                    self._emit(f'  for (int {ai} = 0; {ai} < {ac}; {ai}++) {{')
                    self._emit(f'    if ({cnt} + 1 >= {cap}) {{ {cap} *= 2; {var} = (char **)realloc({var}, sizeof(char *) * (size_t){cap}); }}')
                    self._emit(f'    {var}[{cnt}++] = {ae}[{ai}];')
                    self._emit(f'  }}')
                    self._emit(f'  free({ae}); }}')
                    continue

            # Regular arg
            expr = self._gen_word_expr(w)
            if self._word_needs_free(w):
                tmp = self._tmp('_a')
                self._emit(f'char *{tmp} = (char *){expr};')
                self._add_cleanup(tmp)
                expr = tmp
            else:
                expr = f'(char *){expr}'
            self._emit(f'if ({cnt} + 1 >= {cap}) {{ {cap} *= 2; {var} = (char **)realloc({var}, sizeof(char *) * (size_t){cap}); }}')
            self._emit(f'{var}[{cnt}++] = {expr};')

        self._emit(f'{var}[{cnt}] = NULL;')
        self._add_cleanup(var)
        return (var, cnt)

    # ---------------------------------------------------------------
    #  If
    # ---------------------------------------------------------------

    def _gen_if(self, node: IfNode):
        # Generate condition
        for s in node.condition:
            self._push_cleanup()
            self._gen_statement(s)
            self._pop_cleanup()
        self._emit('if (rt.last_exit == 0) {')
        self.indent += 1
        for s in node.then_body:
            self._push_cleanup()
            self._gen_statement(s)
            self._pop_cleanup()
        self.indent -= 1

        for econd, ebody in node.elifs:
            self._emit('} else {')
            self.indent += 1
            for s in econd:
                self._push_cleanup()
                self._gen_statement(s)
                self._pop_cleanup()
            self._emit('if (rt.last_exit == 0) {')
            self.indent += 1
            for s in ebody:
                self._push_cleanup()
                self._gen_statement(s)
                self._pop_cleanup()
            self.indent -= 1

        if node.else_body:
            self._emit('} else {')
            self.indent += 1
            for s in node.else_body:
                self._push_cleanup()
                self._gen_statement(s)
                self._pop_cleanup()
            self.indent -= 1

        self._emit('}')
        # close the extra braces from elif nesting
        for _ in node.elifs:
            self.indent -= 1
            self._emit('}')

    # ---------------------------------------------------------------
    #  While / Until
    # ---------------------------------------------------------------

    def _gen_while(self, node: WhileNode):
        self._emit('while (1) {')
        self.indent += 1

        for s in node.condition:
            self._push_cleanup()
            self._gen_statement(s)
            self._pop_cleanup()

        if node.until:
            self._emit('if (rt.last_exit == 0) break;')
        else:
            self._emit('if (rt.last_exit != 0) break;')

        for s in node.body:
            self._push_cleanup()
            self._gen_statement(s)
            self._pop_cleanup()

        self.indent -= 1
        self._emit('}')

    # ---------------------------------------------------------------
    #  For
    # ---------------------------------------------------------------

    def _gen_for(self, node: ForNode):
        self._emit('{')
        self.indent += 1

        if node.items is None:
            # for var; do ... done => iterate over $@
            cnt_var = self._tmp('_fc')
            self._emit(f'int {cnt_var} = rt_get_argc();')
            idx_var = self._tmp('_fi')
            self._emit(f'for (int {idx_var} = 1; {idx_var} <= {cnt_var}; {idx_var}++) {{')
            self.indent += 1
            self._emit(f'rt_set_var("{c_escape(node.var)}", rt_get_arg({idx_var}));')
            for s in node.body:
                self._push_cleanup()
                self._gen_statement(s)
                self._pop_cleanup()
            self.indent -= 1
            self._emit('}')
        else:
            # Check if any items need glob expansion
            has_any_glob = any(w.has_glob and not w.quoted for w in node.items)

            if not has_any_glob:
                # Check for array expansion items: ${arr[@]} or ${arr[*]}
                has_array_expand = False
                for w in node.items:
                    if (len(w.parts) == 1 and isinstance(w.parts[0], VarPart)
                            and re.match(r'^[a-zA-Z_]\w*\[[@*]\]$', w.parts[0].name)):
                        has_array_expand = True
                        break

                if has_array_expand and len(node.items) == 1:
                    # Single array expansion: for var in ${arr[@]}
                    vp = node.items[0].parts[0]
                    m = re.match(r'^([a-zA-Z_]\w*)\[[@*]\]$', vp.name)
                    aname = m.group(1)
                    cnt = self._tmp('_ac')
                    arr = self._tmp('_ae')
                    self._emit(f'int {cnt} = 0;')
                    self._emit(f'char **{arr} = rt_array_get_all("{c_escape(aname)}", &{cnt});')
                    idx = self._tmp('_ai')
                    self._emit(f'for (int {idx} = 0; {idx} < {cnt}; {idx}++) {{')
                    self.indent += 1
                    self._emit(f'rt_set_var("{c_escape(node.var)}", {arr}[{idx}]);')
                    for s in node.body:
                        self._push_cleanup()
                        self._gen_statement(s)
                        self._pop_cleanup()
                    self.indent -= 1
                    self._emit('}')
                    self._emit(f'free({arr});')
                else:
                    # Check if any items need word splitting (unquoted var/cmd-subst)
                    needs_split = any(self._word_needs_splitting(w) for w in node.items)

                    if needs_split:
                        # Dynamic iteration: each item may expand to multiple words
                        self._gen_for_body_split(node)
                    else:
                        # Simple case: all items are literals or quoted
                        items_var = self._tmp('_items')
                        item_exprs = []
                        items_to_free = []
                        for w in node.items:
                            expr = self._gen_word_expr(w)
                            if self._word_needs_free(w):
                                tmp = self._tmp('_fi')
                                self._emit(f'char *{tmp} = (char *){expr};')
                                items_to_free.append(tmp)
                                item_exprs.append(tmp)
                            else:
                                item_exprs.append(f'(char *){expr}')

                        self._emit(f'const char *{items_var}[] = {{{", ".join(item_exprs)}}};')
                        idx = self._tmp('_i')
                        self._emit(f'for (int {idx} = 0; {idx} < {len(item_exprs)}; {idx}++) {{')
                        self.indent += 1
                        self._emit(f'rt_set_var("{c_escape(node.var)}", {items_var}[{idx}]);')
                        for s in node.body:
                            self._push_cleanup()
                            self._gen_statement(s)
                            self._pop_cleanup()
                        self.indent -= 1
                        self._emit('}')
                        for t in items_to_free:
                            self._emit(f'free({t});')
            else:
                # Glob expansion case
                for w in node.items:
                    if w.has_glob and not w.quoted:
                        pattern_expr = self._gen_word_expr(w)
                        gc = self._tmp('_gc')
                        gl = self._tmp('_gl')
                        self._emit(f'int {gc} = 0;')
                        self._emit(f'char **{gl} = rt_glob_expand({pattern_expr}, &{gc});')
                        idx = self._tmp('_gi')
                        self._emit(f'for (int {idx} = 0; {idx} < {gc}; {idx}++) {{')
                        self.indent += 1
                        self._emit(f'rt_set_var("{c_escape(node.var)}", {gl}[{idx}]);')
                        for s in node.body:
                            self._push_cleanup()
                            self._gen_statement(s)
                            self._pop_cleanup()
                        self.indent -= 1
                        self._emit('}')
                        self._emit(f'rt_glob_free({gl}, {gc});')
                    else:
                        expr = self._gen_word_expr(w)
                        needs_free = self._word_needs_free(w)
                        if needs_free:
                            tmp = self._tmp('_fv')
                            self._emit(f'char *{tmp} = (char *){expr};')
                            self._emit(f'rt_set_var("{c_escape(node.var)}", {tmp});')
                        else:
                            self._emit(f'rt_set_var("{c_escape(node.var)}", {expr});')
                        self._emit('{')
                        self.indent += 1
                        for s in node.body:
                            self._push_cleanup()
                            self._gen_statement(s)
                            self._pop_cleanup()
                        self.indent -= 1
                        self._emit('}')
                        if needs_free:
                            self._emit(f'free({tmp});')

        self.indent -= 1
        self._emit('}')

    @staticmethod
    def _word_needs_splitting(w: Word) -> bool:
        """Check if an unquoted word contains expansions that need IFS splitting."""
        if w.quoted:
            return False
        for p in w.parts:
            if isinstance(p, VarPart):
                # Special vars that expand to multiple words
                if p.name in ('@', '*'):
                    return False  # handled separately
                # Array expansions handled separately
                if re.match(r'^[a-zA-Z_]\w*\[[@*]\]$', p.name):
                    return False
                # Regular variable — needs word splitting when unquoted
                return True
            if isinstance(p, CmdSubstPart):
                return True
        return False

    def _gen_for_body_split(self, node: ForNode):
        """Generate for-loop body where items may need word splitting."""
        for w in node.items:
            if self._word_needs_splitting(w):
                # Evaluate the word, then split by IFS
                expr = self._gen_word_expr(w)
                needs_free = self._word_needs_free(w)
                val_var = self._tmp('_sv')
                if needs_free:
                    self._emit(f'char *{val_var} = (char *){expr};')
                else:
                    self._emit(f'const char *{val_var} = {expr};')
                wc = self._tmp('_wc')
                wv = self._tmp('_wv')
                self._emit(f'int {wc} = 0;')
                self._emit(f'char **{wv} = rt_split_words({val_var}, &{wc});')
                if needs_free:
                    self._emit(f'free({val_var});')
                wi = self._tmp('_wi')
                self._emit(f'for (int {wi} = 0; {wi} < {wc}; {wi}++) {{')
                self.indent += 1
                self._emit(f'rt_set_var("{c_escape(node.var)}", {wv}[{wi}]);')
                for s in node.body:
                    self._push_cleanup()
                    self._gen_statement(s)
                    self._pop_cleanup()
                self.indent -= 1
                self._emit('}')
                self._emit(f'rt_split_free({wv}, {wc});')
            else:
                # Single literal/quoted value — iterate once
                expr = self._gen_word_expr(w)
                needs_free = self._word_needs_free(w)
                if needs_free:
                    tmp = self._tmp('_fv')
                    self._emit(f'char *{tmp} = (char *){expr};')
                    self._emit(f'rt_set_var("{c_escape(node.var)}", {tmp});')
                else:
                    self._emit(f'rt_set_var("{c_escape(node.var)}", {expr});')
                self._emit('{')
                self.indent += 1
                for s in node.body:
                    self._push_cleanup()
                    self._gen_statement(s)
                    self._pop_cleanup()
                self.indent -= 1
                self._emit('}')
                if needs_free:
                    self._emit(f'free({tmp});')

    # ---------------------------------------------------------------
    #  C-style For
    # ---------------------------------------------------------------

    def _gen_cfor(self, node: CForNode):
        if node.init:
            self._emit(f'rt_arith_eval("{c_escape(node.init)}");')
        self._emit(f'while (rt_arith_eval("{c_escape(node.cond)}")) {{')
        self.indent += 1
        for s in node.body:
            self._push_cleanup()
            self._gen_statement(s)
            self._pop_cleanup()
        if node.step:
            self._emit(f'rt_arith_eval("{c_escape(node.step)}");')
        self.indent -= 1
        self._emit('}')

    # ---------------------------------------------------------------
    #  Case
    # ---------------------------------------------------------------

    def _gen_case(self, node: CaseNode):
        word_expr = self._gen_word_expr(node.word)
        word_free = self._word_needs_free(node.word)
        word_var = self._tmp('_cw')
        self._emit(f'char *{word_var} = rt_strdup_safe({word_expr});')

        first = True
        for item in node.items:
            conditions = []
            for pat in item.patterns:
                pat_val = pat.literal_value if pat.is_simple_literal else None
                if pat_val == '*':
                    conditions.append('1')
                elif pat_val is not None:
                    conditions.append(f'fnmatch("{c_escape(pat_val)}", {word_var}, 0) == 0')
                else:
                    pe = self._gen_word_expr(pat)
                    conditions.append(f'fnmatch({pe}, {word_var}, 0) == 0')

            cond_str = ' || '.join(conditions) if conditions else '0'
            keyword = 'if' if first else 'else if'
            self._emit(f'{keyword} ({cond_str}) {{')
            self.indent += 1
            for s in item.body:
                self._push_cleanup()
                self._gen_statement(s)
                self._pop_cleanup()
            self.indent -= 1
            self._emit('}')
            first = False

        self._emit(f'free({word_var});')

    # ---------------------------------------------------------------
    #  Word expression generation
    # ---------------------------------------------------------------

    def _var_access_needs_free(self, name: str) -> bool:
        """Check whether _gen_var_access for this name returns a heap-allocated string."""
        if name in ('?', '#', '$', '@', '*', '!'):
            return True
        # ${#var} – string length
        if name.startswith('#') and '[' not in name:
            return True
        # ${#arr[@]} / ${#arr[*]} – array length
        if name.startswith('#') and re.match(r'^#[a-zA-Z_]\w*\[[@*]\]$', name):
            return True
        # ${arr[@]} / ${arr[*]} – array join
        if re.match(r'^[a-zA-Z_]\w*\[[@*]\]$', name):
            return True
        # ${arr[idx]} – const pointer, no free
        if re.match(r'^[a-zA-Z_]\w*\[.+\]$', name):
            return False
        return False

    def _word_needs_free(self, w: Word) -> bool:
        """Check if the generated expression for this word needs to be freed."""
        if len(w.parts) == 0:
            return False
        if len(w.parts) == 1:
            p = w.parts[0]
            if isinstance(p, (LiteralPart, SingleQuotedPart)):
                return False  # string literal
            if isinstance(p, VarPart):
                return self._var_access_needs_free(p.name)
            if isinstance(p, (CmdSubstPart, ArithPart)):
                return True
        # multi-part words use bstr, always need free
        return True if len(w.parts) > 1 else False

    def _gen_word_expr(self, w: Word) -> str:
        """Generate a C expression evaluating to a char* for this word."""
        if len(w.parts) == 0:
            return '""'

        if len(w.parts) == 1:
            return self._gen_part_expr(w.parts[0])

        # Multi-part: build with BStr
        bvar = self._tmp('_b')
        self._emit(f'BStr {bvar} = bstr_new();')

        for part in w.parts:
            if isinstance(part, (LiteralPart, SingleQuotedPart)):
                self._emit(f'bstr_append(&{bvar}, "{c_escape(part.text)}");')
            elif isinstance(part, VarPart):
                access = self._gen_var_access(part.name)
                if self._var_access_needs_free(part.name):
                    tmp = self._tmp('_sv')
                    self._emit(f'char *{tmp} = {access};')
                    self._emit(f'bstr_append(&{bvar}, {tmp});')
                    self._emit(f'free({tmp});')
                else:
                    self._emit(f'bstr_append(&{bvar}, {access});')
            elif isinstance(part, CmdSubstPart):
                tmp = self._tmp('_cs')
                cs_expr = self._gen_cmd_subst_expr(part.command)
                self._emit(f'char *{tmp} = {cs_expr};')
                self._emit(f'bstr_append(&{bvar}, {tmp});')
                self._emit(f'free({tmp});')
            elif isinstance(part, ArithPart):
                tmp = self._tmp('_ar')
                self._emit(f'char *{tmp} = rt_arith_str("{c_escape(part.expr)}");')
                self._emit(f'bstr_append(&{bvar}, {tmp});')
                self._emit(f'free({tmp});')

        result = self._tmp('_w')
        self._emit(f'char *{result} = bstr_release(&{bvar});')
        return result

    def _gen_part_expr(self, part: WordPart) -> str:
        """Generate C expression for a single word part."""
        if isinstance(part, LiteralPart):
            return f'"{c_escape(part.text)}"'
        if isinstance(part, SingleQuotedPart):
            return f'"{c_escape(part.text)}"'
        if isinstance(part, VarPart):
            return self._gen_var_access(part.name)
        if isinstance(part, CmdSubstPart):
            return self._gen_cmd_subst_expr(part.command)
        if isinstance(part, ArithPart):
            return f'rt_arith_str("{c_escape(part.expr)}")'
        return '""'

    def _gen_cmd_subst_expr(self, cmd_str: str) -> str:
        """Generate a C expression for a command substitution.
        Handles here-strings (<<<) by pre-evaluating the input in the parent
        and using rt_cmd_subst_stdin instead of rt_cmd_subst."""

        # Detect <<< here-string pattern
        hs_match = re.match(r'^(.*?)\s*<<<\s*(.+)$', cmd_str)
        if hs_match:
            cmd_part = hs_match.group(1).strip()
            hs_content = hs_match.group(2).strip()

            # Strip surrounding quotes from here-string content
            if ((hs_content.startswith('"') and hs_content.endswith('"')) or
                (hs_content.startswith("'") and hs_content.endswith("'"))):
                hs_content = hs_content[1:-1]

            # Check for ${arr[*]} or ${arr[@]} patterns
            arr_m = re.match(r'^\$\{([a-zA-Z_]\w*)\[[@*]\]\}$', hs_content)
            if arr_m:
                aname = arr_m.group(1)
                # Pre-evaluate array join in parent, pipe to command
                hs_var = self._tmp('_hs')
                self._emit(f'char *{hs_var} = rt_array_join("{c_escape(aname)}", "\\n");')
                result_var = self._tmp('_cr')
                self._emit(f'char *{result_var} = rt_cmd_subst_stdin("{c_escape(cmd_part)}", {hs_var});')
                self._emit(f'free({hs_var});')
                return result_var

            # Check for $var pattern
            var_m = re.match(r'^\$\{?([a-zA-Z_]\w*)\}?$', hs_content)
            if var_m:
                vname = var_m.group(1)
                result_var = self._tmp('_cr')
                self._emit(f'char *{result_var} = rt_cmd_subst_stdin("{c_escape(cmd_part)}", rt_get_var("{c_escape(vname)}"));')
                return result_var

            # Literal here-string
            result_var = self._tmp('_cr')
            self._emit(f'char *{result_var} = rt_cmd_subst_stdin("{c_escape(cmd_part)}", "{c_escape(hs_content)}");')
            return result_var

        # Regular command substitution
        return f'rt_cmd_subst("{c_escape(cmd_str)}")'

    @staticmethod
    def _clean_arith_expr(expr: str) -> str:
        """Clean an index expression for rt_arith_eval.
        Strips $ prefixes from variable references (bash allows $var or var
        inside arithmetic contexts) and unwraps $((...)).
        """
        # Strip surrounding $(( )) if present
        e = expr.strip()
        if e.startswith('$((') and e.endswith('))'):
            e = e[3:-2]
        # Replace $var references with plain var names
        e = re.sub(r'\$([a-zA-Z_]\w*)', r'\1', e)
        return e

    def _gen_var_access(self, name: str) -> str:
        """Generate C expression to access a bash variable."""
        if name == '?':
            return 'rt_itoa(rt.last_exit)'
        elif name == '#':
            return 'rt_itoa(rt_get_argc())'
        elif name == '@' or name == '*':
            return 'rt_join_args(" ")'
        elif name == '$':
            return 'rt_itoa((long)getpid())'
        elif name == '!':
            return '(rt.last_bg_pid > 0 ? rt_itoa((long)rt.last_bg_pid) : rt_strdup_safe(""))'
        elif name == '-':
            return '""'  # simplified
        elif name == '0':
            return 'rt_get_arg0()'
        elif name.isdigit():
            return f'rt_get_arg({name})'

        # ${#var} – string length (no brackets)
        m = re.match(r'^#([a-zA-Z_]\w*)$', name)
        if m:
            vname = m.group(1)
            return f'rt_itoa((long)strlen(rt_get_var("{c_escape(vname)}")))'

        # ${#arr[@]} / ${#arr[*]} – array element count
        m = re.match(r'^#([a-zA-Z_]\w*)\[[@*]\]$', name)
        if m:
            aname = m.group(1)
            return f'rt_itoa(rt_array_len("{c_escape(aname)}"))'

        # ${arr[@]} / ${arr[*]} – join all elements
        m = re.match(r'^([a-zA-Z_]\w*)\[[@*]\]$', name)
        if m:
            aname = m.group(1)
            return f'rt_array_join("{c_escape(aname)}", " ")'

        # ${arr[expr]} – single element access
        m = re.match(r'^([a-zA-Z_]\w*)\[(.+)\]$', name)
        if m:
            aname = m.group(1)
            idx_expr = self._clean_arith_expr(m.group(2))
            return f'rt_array_get("{c_escape(aname)}", (int)rt_arith_eval("{c_escape(idx_expr)}"))'

        # plain variable
        return f'rt_get_var("{c_escape(name)}")'


# ====================================================================
#  Main
# ====================================================================

def transpile(source: str) -> str:
    tokenizer = Tokenizer(source)
    parser = Parser(tokenizer.tokens)
    stmts = parser.parse()
    gen = CodeGen()
    return gen.generate(stmts)


def main():
    import argparse
    import platform
    import subprocess
    import tempfile
    import shutil

    # ── Resolve the runtime directory relative to this script ──
    script_dir = os.path.dirname(os.path.abspath(__file__))
    runtime_dir = os.path.join(script_dir, 'runtime')
    runtime_c   = os.path.join(runtime_dir, 'bash_runtime.c')
    runtime_h   = os.path.join(runtime_dir, 'bash_runtime.h')

    # ── CLI ──
    p = argparse.ArgumentParser(
        prog='shellraiser',
        description='Transpile Bash scripts to C and compile them into native binaries.',
        epilog='Examples:\n'
               '  shellraiser script.sh                  → ./script\n'
               '  shellraiser script.sh -o mybin          → ./mybin\n'
               '  shellraiser script.sh --emit-only       → prints C to stdout\n'
               '  shellraiser script.sh --save-source     → keeps script.c alongside binary\n'
               '  shellraiser script.sh --compiler clang --cflags="-O2 -g"\n',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument('script', help='Bash script to transpile')
    p.add_argument('-o', '--output', default=None,
                   help='Output binary path (default: <script> minus extension, in same dir)')
    p.add_argument('--compiler', default=None,
                   help='C compiler path (default: gcc on Linux, clang on macOS)')
    p.add_argument('--cflags', default=None,
                   help='Custom compiler flags (default: "-O3 -Wall")')
    p.add_argument('--emit-only', action='store_true',
                   help='Print generated C to stdout and exit (no compilation)')
    p.add_argument('--save-source', action='store_true',
                   help='Keep the generated .c file after compilation')
    p.add_argument('-v', '--verbose', action='store_true',
                   help='Print compiler command before running it')

    args = p.parse_args()

    # ── Read source ──
    try:
        with open(args.script, 'r') as f:
            source = f.read()
    except FileNotFoundError:
        print(f"shellraiser: error: file '{args.script}' not found", file=sys.stderr)
        sys.exit(1)

    if source.startswith('#!'):
        source = source[source.index('\n'):]

    # ── Transpile ──
    try:
        c_code = transpile(source)
    except ParseError as e:
        print(f"shellraiser: parse error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"shellraiser: error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)

    # ── Emit-only mode: just print C and exit ──
    if args.emit_only:
        print(c_code)
        sys.exit(0)

    # ── Validate runtime exists ──
    for path, label in [(runtime_c, 'bash_runtime.c'), (runtime_h, 'bash_runtime.h')]:
        if not os.path.isfile(path):
            print(f"shellraiser: error: runtime file '{label}' not found at {path}\n"
                  f"  Expected layout: shellraiser.py and runtime/ in the same directory.",
                  file=sys.stderr)
            sys.exit(1)

    # ── Determine output binary path ──
    if args.output:
        out_bin = args.output
    else:
        base = os.path.basename(args.script)
        name_no_ext = os.path.splitext(base)[0]
        out_bin = os.path.join(os.path.dirname(os.path.abspath(args.script)), name_no_ext)

    # ── Determine where to write the .c file ──
    if args.save_source:
        c_path = os.path.splitext(os.path.abspath(args.script))[0] + '.c'
    else:
        # Use a temp file that we'll clean up
        tmp_fd, c_path = tempfile.mkstemp(suffix='.c', prefix='shellraiser_')
        os.close(tmp_fd)

    try:
        # ── Write C source ──
        with open(c_path, 'w') as f:
            f.write(c_code)

        # ── Pick compiler ──
        if args.compiler:
            cc = args.compiler
        elif platform.system() == 'Darwin':
            cc = 'clang'
        else:
            cc = 'gcc'

        # Verify compiler is available
        cc_resolved = shutil.which(cc)
        if cc_resolved is None:
            print(f"shellraiser: error: compiler '{cc}' not found on PATH", file=sys.stderr)
            sys.exit(1)

        # ── Build compiler flags ──
        if args.cflags is not None:
            user_flags = args.cflags.split()
        else:
            user_flags = ['-O3', '-Wall']

        cmd = [
            cc_resolved,
            *user_flags,
            '-std=c11',
            f'-I{runtime_dir}',
            c_path,
            runtime_c,
            '-o', out_bin,
        ]

        if args.verbose:
            print(' '.join(cmd), file=sys.stderr)

        # ── Compile ──
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            # Show all stderr on failure, but still filter useless warnings
            suppress = ('find_in_path', 'unused-function')
            lines = result.stderr.splitlines()
            stderr_lines = []
            skipping = False
            for line in lines:
                if any(s in line for s in suppress):
                    skipping = True
                    continue
                if skipping:
                    stripped = line.lstrip()
                    if (stripped.startswith(('|', '^', '~'))
                            or stripped[:1].isdigit()
                            or not stripped):
                        continue
                    skipping = False
                stderr_lines.append(line)
            if stderr_lines:
                print('\n'.join(stderr_lines), file=sys.stderr)
            print(f"shellraiser: compilation failed (exit {result.returncode})", file=sys.stderr)
            sys.exit(1)

        # Print non-fatal warnings (excluding noisy ones)
        if result.stderr:
            suppress = ('find_in_path', 'unused-function')
            lines = result.stderr.splitlines()
            filtered = []
            skipping = False
            for line in lines:
                if any(s in line for s in suppress):
                    skipping = True
                    continue
                if skipping:
                    # GCC continuation lines: source refs, carets, notes
                    stripped = line.lstrip()
                    if (stripped.startswith(('|', '^', '~'))
                            or stripped[:1].isdigit()
                            or not stripped):
                        continue
                    skipping = False
                filtered.append(line)
            if filtered:
                print('\n'.join(filtered), file=sys.stderr)

        # ── chmod +x ──
        os.chmod(out_bin, 0o755)

        print(f"shellraiser: compiled → {out_bin}", file=sys.stderr)

    finally:
        # ── Cleanup intermediate .c if not saving ──
        if not args.save_source and os.path.exists(c_path):
            os.unlink(c_path)


if __name__ == '__main__':
    main()