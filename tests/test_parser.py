"""
Parser tests. The parser exposes Parser(tokens).parse() -> list of Statement
AST nodes. We check that representative constructs produce the documented node
types (IfNode, ForNode, FunctionDef, etc.) without asserting on internal field
layouts that may evolve.
"""

import pytest


def _parse(sr, source):
    toks = sr.Tokenizer(source).tokens
    return sr.Parser(toks).parse()


def _node_type_names(stmts):
    return {type(s).__name__ for s in stmts}


def test_assignment_node(sr):
    stmts = _parse(sr, "x=5\n")
    assert "AssignmentNode" in _node_type_names(stmts)


def test_if_node(sr):
    stmts = _parse(sr, "if true; then echo y; fi\n")
    assert "IfNode" in _node_type_names(stmts)


def test_while_node(sr):
    stmts = _parse(sr, "while false; do echo x; done\n")
    assert "WhileNode" in _node_type_names(stmts)


def test_for_in_node(sr):
    stmts = _parse(sr, "for i in 1 2 3; do echo $i; done\n")
    assert "ForNode" in _node_type_names(stmts)


def test_c_style_for_node(sr):
    stmts = _parse(sr, "for (( i=0; i<3; i++ )); do echo $i; done\n")
    assert "CForNode" in _node_type_names(stmts)


def test_case_node(sr):
    stmts = _parse(sr, "case $x in a) echo a;; *) echo o;; esac\n")
    assert "CaseNode" in _node_type_names(stmts)


def test_function_def_short_form(sr):
    stmts = _parse(sr, "greet() { echo hi; }\n")
    assert "FunctionDef" in _node_type_names(stmts)


def test_function_def_keyword_form(sr):
    stmts = _parse(sr, "function greet { echo hi; }\n")
    assert "FunctionDef" in _node_type_names(stmts)


def test_array_list_assignment(sr):
    stmts = _parse(sr, "arr=(a b c)\n")
    assert "ArrayListAssign" in _node_type_names(stmts)


def test_array_append_assignment(sr):
    stmts = _parse(sr, "arr+=(d)\n")
    assert "ArrayAppendAssign" in _node_type_names(stmts)


def test_multiple_top_level_statements(sr):
    stmts = _parse(sr, "x=1\ny=2\necho done\n")
    assert len(stmts) >= 3


@pytest.mark.parametrize("bad", [
    "if true; then echo x\n",          # missing fi
    "for i in 1 2; do echo $i\n",      # missing done
    "case $x in a) echo a;;\n",        # missing esac
    "greet() { echo hi\n",             # missing closing brace
])
def test_malformed_input_raises_parse_error(sr, bad):
    with pytest.raises(sr.ParseError):
        _parse(sr, bad)
