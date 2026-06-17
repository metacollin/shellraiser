"""
Tokenizer tests. The tokenizer exposes Tokenizer(source).tokens, a list of
Token objects with a `.type` (TT enum) and `.value`.
"""

import pytest


def _types(sr, source):
    toks = sr.Tokenizer(source).tokens
    return [t.type for t in toks]


def _values(sr, source):
    return [t.value for t in sr.Tokenizer(source).tokens]


def test_simple_words(sr):
    types = _types(sr, "echo hello")
    assert sr.TT.WORD in types


def test_pipe_token(sr):
    assert sr.TT.PIPE in _types(sr, "a | b")


def test_and_or_tokens(sr):
    assert sr.TT.AND in _types(sr, "a && b")
    assert sr.TT.OR in _types(sr, "a || b")


def test_redirection_tokens(sr):
    assert sr.TT.REDIR_OUT in _types(sr, "echo hi > f")
    assert sr.TT.REDIR_APP in _types(sr, "echo hi >> f")
    assert sr.TT.REDIR_IN in _types(sr, "cat < f")


def test_semicolon_and_newline(sr):
    assert sr.TT.SEMI in _types(sr, "a ; b")
    assert sr.TT.NEWLINE in _types(sr, "a\nb")


def test_keywords_tokenized(sr):
    types = _types(sr, "if true; then echo x; fi")
    assert sr.TT.IF in types
    assert sr.TT.THEN in types
    assert sr.TT.FI in types


def test_for_keywords(sr):
    types = _types(sr, "for i in 1 2; do echo $i; done")
    for member in (sr.TT.FOR, sr.TT.IN, sr.TT.DO, sr.TT.DONE):
        assert member in types


def test_comment_is_skipped(sr):
    # A pure comment line should not yield WORD tokens for its text.
    types = _types(sr, "# just a comment\n")
    assert sr.TT.WORD not in types


def test_token_stream_ends_with_eof(sr):
    toks = sr.Tokenizer("echo hi\n").tokens
    # Some tokenizers append EOF; if so it must be last. If not, that's fine too,
    # but when present it must terminate the stream.
    if any(t.type == sr.TT.EOF for t in toks):
        assert toks[-1].type == sr.TT.EOF


def test_double_semicolon_for_case(sr):
    types = _types(sr, "case $x in a) echo a;; esac")
    assert sr.TT.DSEMI in types


def test_line_continuation_joins(sr):
    # A backslash-newline should not produce a NEWLINE token at that point.
    toks = sr.Tokenizer("echo a \\\nb\n").tokens
    # There should be a trailing newline (end of the logical line) but the
    # continuation in the middle should have been consumed.
    newline_count = sum(1 for t in toks if t.type == sr.TT.NEWLINE)
    assert newline_count == 1
