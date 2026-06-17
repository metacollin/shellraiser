"""
End-to-end: builtins. echo (-n, -e), printf (formats, repetition, %b, width),
test/[ operators, and [[ ]] extended test (==, !=, =~, &&, ||, !).
"""

import pytest


# ---- echo ----

def test_echo_basic(run_bash):
    r = run_bash('echo hello world\n')
    assert r.stdout == "hello world\n"


def test_echo_no_newline(run_bash):
    r = run_bash('echo -n nonewline\n')
    assert r.stdout == "nonewline"


def test_echo_escape_interpretation(run_bash):
    r = run_bash('echo -e "a\\tb"\n')
    assert r.stdout == "a\tb\n"


def test_echo_escape_off_by_default(run_bash):
    # Without -e, \t stays literal.
    r = run_bash('echo "a\\tb"\n')
    assert r.stdout == "a\\tb\n"


# ---- printf ----

def test_printf_string(run_bash):
    r = run_bash('printf "%s\\n" hi\n')
    assert r.stdout == "hi\n"


def test_printf_integer(run_bash):
    r = run_bash('printf "%d\\n" 42\n')
    assert r.stdout == "42\n"


def test_printf_format_repetition(run_bash):
    # printf reuses the format for each remaining arg.
    r = run_bash('printf "%s\\n" a b c\n')
    assert r.stdout == "a\nb\nc\n"


def test_printf_width(run_bash):
    r = run_bash('printf "%5d\\n" 7\n')
    assert r.stdout == "    7\n"


def test_printf_hex(run_bash):
    r = run_bash('printf "%x\\n" 255\n')
    assert r.stdout == "ff\n"


def test_printf_percent_b_escapes(run_bash):
    r = run_bash('printf "%b\\n" "x\\ty"\n')
    assert r.stdout == "x\ty\n"


def test_printf_v_assigns_variable(run_bash):
    r = run_bash('printf -v out "%d" 99\necho $out\n')
    assert r.stdout == "99\n"


# ---- test / [ ----

def test_bracket_string_equality(run_bash):
    r = run_bash('if [ "abc" = "abc" ]; then echo eq; fi\n')
    assert r.stdout == "eq\n"


def test_bracket_numeric_lt(run_bash):
    r = run_bash('if [ 3 -lt 5 ]; then echo less; fi\n')
    assert r.stdout == "less\n"


def test_bracket_string_nonempty(run_bash):
    r = run_bash('s=x\nif [ -n "$s" ]; then echo nonempty; fi\n')
    assert r.stdout == "nonempty\n"


def test_bracket_string_empty(run_bash):
    r = run_bash('s=""\nif [ -z "$s" ]; then echo empty; fi\n')
    assert r.stdout == "empty\n"


def test_bracket_file_exists(run_bash, tmp_path):
    f = tmp_path / "exists.txt"
    f.write_text("hi")
    r = run_bash(f'if [ -f {f} ]; then echo found; fi\n')
    assert r.stdout == "found\n"


# ---- [[ ]] extended test ----

def test_double_bracket_glob_match(run_bash):
    r = run_bash('f=a.txt\nif [[ $f == *.txt ]]; then echo txt; fi\n')
    assert r.stdout == "txt\n"


def test_double_bracket_not_equal(run_bash):
    r = run_bash('if [[ abc != xyz ]]; then echo diff; fi\n')
    assert r.stdout == "diff\n"


def test_double_bracket_regex(run_bash):
    r = run_bash('s=hello123\nif [[ $s =~ [0-9]+ ]]; then echo hasnum; fi\n')
    assert r.stdout == "hasnum\n"


def test_double_bracket_logical_and(run_bash):
    r = run_bash('a=1\nb=2\nif [[ $a == 1 && $b == 2 ]]; then echo both; fi\n')
    assert r.stdout == "both\n"


def test_double_bracket_logical_or(run_bash):
    r = run_bash('x=no\nif [[ $x == yes || $x == no ]]; then echo matched; fi\n')
    assert r.stdout == "matched\n"


def test_double_bracket_negation(run_bash):
    r = run_bash('if [[ ! abc == xyz ]]; then echo negated; fi\n')
    assert r.stdout == "negated\n"


# ---- misc builtins ----

def test_true_false_exit_codes(run_bash):
    r = run_bash('true; echo $?\nfalse; echo $?\n')
    assert r.stdout == "0\n1\n"


def test_colon_noop(run_bash):
    r = run_bash(':\necho after\n')
    assert r.stdout == "after\n"


def test_command_v_succeeds_for_known_command(run_bash):
    r = run_bash('if command -v echo; then echo present; fi\n')
    assert r.stdout.strip().splitlines()[-1] == "present"


def test_command_v_fails_for_missing_command(run_bash):
    r = run_bash(
        'if command -v definitely_not_a_real_cmd_zzz; then echo found\n'
        'else echo missing; fi\n'
    )
    assert r.stdout.strip().splitlines()[-1] == "missing"