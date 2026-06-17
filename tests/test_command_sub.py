"""
End-to-end: command substitution $(...) and backticks, special variables
($?, $#, $0), subshells, and glob expansion in command arguments.
"""


def test_command_substitution_dollar_paren(run_bash):
    r = run_bash('x=$(echo inner)\necho $x\n')
    assert r.stdout == "inner\n"


def test_command_substitution_backticks(run_bash):
    r = run_bash('x=`echo back`\necho $x\n')
    assert r.stdout == "back\n"


def test_command_substitution_in_string(run_bash):
    r = run_bash('echo "result: $(echo ok)"\n')
    assert r.stdout == "result: ok\n"


def test_nested_command_substitution(run_bash):
    r = run_bash('echo $(echo $(echo deep))\n')
    assert r.stdout == "deep\n"


def test_exit_status_variable(run_bash):
    r = run_bash('true\necho $?\n')
    assert r.stdout == "0\n"


def test_exit_status_after_failure(run_bash):
    r = run_bash('false\necho $?\n')
    assert r.stdout == "1\n"


def test_arg_count_variable(run_bash):
    r = run_bash('echo $#\n', args=["a", "b", "c"])
    assert r.stdout == "3\n"


def test_positional_args(run_bash):
    r = run_bash('echo "$1 $2"\n', args=["first", "second"])
    assert r.stdout == "first second\n"


def test_explicit_exit_code(run_bash):
    r = run_bash('exit 7\n')
    assert r.returncode == 7


def test_subshell_isolation(run_bash):
    # A variable set inside ( ) does not affect the parent.
    src = (
        'x=outer\n'
        '( x=inner; echo $x )\n'
        'echo $x\n'
    )
    r = run_bash(src)
    assert r.stdout == "inner\nouter\n"


def test_glob_expansion(run_bash, tmp_path):
    (tmp_path / "one.log").write_text("")
    (tmp_path / "two.log").write_text("")
    (tmp_path / "skip.txt").write_text("")
    r = run_bash(f'cd {tmp_path}\nfor f in *.log; do echo $f; done\n')
    assert sorted(r.stdout.split()) == ["one.log", "two.log"]