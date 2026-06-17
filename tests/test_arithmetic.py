"""
End-to-end: arithmetic expansion $(( ... )) with C operators.
"""

import pytest


@pytest.mark.parametrize("expr,expected", [
    ("1 + 2", "3"),
    ("10 - 4", "6"),
    ("3 * 4", "12"),
    ("20 / 6", "3"),       # integer division
    ("20 % 6", "2"),
    ("2 * (3 + 4)", "14"),
    ("1 << 4", "16"),
    ("255 & 15", "15"),
    ("8 | 1", "9"),
    ("5 ^ 1", "4"),
    ("10 > 3", "1"),
    ("3 > 10", "0"),
    ("5 == 5", "1"),
    ("5 != 5", "0"),
])
def test_arithmetic_expressions(run_bash, expr, expected):
    r = run_bash(f'echo $(( {expr} ))\n')
    assert r.stdout == expected + "\n"


def test_arithmetic_with_variables(run_bash):
    r = run_bash('a=6\nb=7\necho $(( a * b ))\n')
    assert r.stdout == "42\n"


def test_arithmetic_accumulate_in_loop(run_bash):
    src = (
        'sum=0\n'
        'for i in 1 2 3 4 5; do sum=$(( sum + i )); done\n'
        'echo $sum\n'
    )
    r = run_bash(src)
    assert r.stdout == "15\n"


def test_nested_arithmetic(run_bash):
    r = run_bash('echo $(( (2 + 3) * (4 - 1) ))\n')
    assert r.stdout == "15\n"


def test_unary_negation(run_bash):
    r = run_bash('echo $(( -5 + 8 ))\n')
    assert r.stdout == "3\n"
