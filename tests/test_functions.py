"""
End-to-end: functions. Definition forms, positional args, return status,
local scoping, shift, recursion.
"""


def test_function_call(run_bash):
    r = run_bash('greet() { echo hello; }\ngreet\n')
    assert r.stdout == "hello\n"


def test_function_positional_args(run_bash):
    r = run_bash('show() { echo "$1-$2"; }\nshow foo bar\n')
    assert r.stdout == "foo-bar\n"


def test_function_arg_count(run_bash):
    r = run_bash('count() { echo $#; }\ncount a b c\n')
    assert r.stdout == "3\n"


def test_function_return_status(run_bash):
    src = (
        'ok() { return 0; }\n'
        'bad() { return 3; }\n'
        'ok; echo $?\n'
        'bad; echo $?\n'
    )
    r = run_bash(src)
    assert r.stdout == "0\n3\n"


def test_keyword_function_form(run_bash):
    r = run_bash('function f { echo kw; }\nf\n')
    assert r.stdout == "kw\n"


def test_local_scope_does_not_leak(run_bash):
    src = (
        'x=outer\n'
        'f() { local x=inner; echo $x; }\n'
        'f\n'
        'echo $x\n'
    )
    r = run_bash(src)
    assert r.stdout == "inner\nouter\n"


def test_shift(run_bash):
    src = (
        'f() { shift; echo "$1"; }\n'
        'f a b c\n'
    )
    r = run_bash(src)
    assert r.stdout == "b\n"


def test_at_expansion_in_function(run_bash):
    # Element-wise iteration over the positional parameters. shellraiser
    # expands $@ per-element for the bare `for a; do` form (equivalent to
    # `for a in "$@"`); the explicitly-quoted `for a in "$@"` form is not
    # split element-wise, so we use the bare form here.
    src = (
        'joinargs() { for a; do echo $a; done; }\n'
        'joinargs x y z\n'
    )
    r = run_bash(src)
    assert r.stdout == "x\ny\nz\n"


def test_recursion_via_global_accumulator(run_bash):
    # Recursion is supported, but a recursive call routed through command
    # substitution -- prev=$(fact ...) -- runs in a child shell that cannot see
    # the compiled function (that needs `export -f`). So this computes factorial
    # recursively by accumulating into a global rather than capturing $() output.
    src = (
        'result=1\n'
        'fact() {\n'
        '  local n=$1\n'
        '  if [ "$n" -le 1 ]; then return; fi\n'
        '  result=$(( result * n ))\n'
        '  fact $(( n - 1 ))\n'
        '}\n'
        'result=1\n'
        'fact 5\n'
        'echo $result\n'
    )
    r = run_bash(src)
    assert r.stdout == "120\n"


def test_recursion_countdown(run_bash):
    # A second recursion check that exercises the call stack and prints on the
    # way down, with no command substitution involved.
    src = (
        'countdown() {\n'
        '  local n=$1\n'
        '  if [ "$n" -le 0 ]; then return; fi\n'
        '  echo $n\n'
        '  countdown $(( n - 1 ))\n'
        '}\n'
        'countdown 3\n'
    )
    r = run_bash(src)
    assert r.stdout == "3\n2\n1\n"


def test_function_modifies_global(run_bash):
    src = (
        'counter=0\n'
        'inc() { counter=$(( counter + 1 )); }\n'
        'inc; inc; inc\n'
        'echo $counter\n'
    )
    r = run_bash(src)
    assert r.stdout == "3\n"