"""
End-to-end: variables, quoting, expansion, ANSI-C quoting, string append.
Only features marked supported in the README matrix are exercised here.
"""


def test_simple_assignment_and_expansion(run_bash):
    r = run_bash('x=hello\necho $x\n')
    assert r.stdout == "hello\n"
    assert r.returncode == 0


def test_braced_expansion(run_bash):
    r = run_bash('name=world\necho ${name}\n')
    assert r.stdout == "world\n"


def test_double_quotes_preserve_spaces(run_bash):
    r = run_bash('x="a   b"\necho "$x"\n')
    assert r.stdout == "a   b\n"


def test_single_quotes_are_literal(run_bash):
    r = run_bash("echo 'no $expansion here'\n")
    assert r.stdout == "no $expansion here\n"


def test_concatenation_of_var_and_literal(run_bash):
    r = run_bash('p=/tmp\necho ${p}/file\n')
    assert r.stdout == "/tmp/file\n"


def test_string_length(run_bash):
    r = run_bash('s=abcd\necho ${#s}\n')
    assert r.stdout == "4\n"


def test_in_place_append(run_bash):
    r = run_bash('s=foo\ns+=bar\necho $s\n')
    assert r.stdout == "foobar\n"


def test_ansi_c_quoting_newline(run_bash):
    # $'\n' should embed an actual newline.
    r = run_bash("printf '%s' $'a\\nb'\n")
    assert r.stdout == "a\nb"


def test_ansi_c_quoting_tab(run_bash):
    r = run_bash("printf '%s' $'x\\ty'\n")
    assert r.stdout == "x\ty"


def test_unset_variable_expands_empty(run_bash):
    r = run_bash('echo "[$undefined]"\n')
    assert r.stdout == "[]\n"


def test_reassignment(run_bash):
    r = run_bash('x=1\nx=2\necho $x\n')
    assert r.stdout == "2\n"
