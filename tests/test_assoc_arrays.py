"""
End-to-end: associative arrays (declare -A). Set/get by key, key iteration
via ${!map[@]}, entry count via ${#map[@]}.

Key-iteration order is not guaranteed. Bash would normally let you pipe the
loop into `sort`, but shellraiser does not support a compound command (a loop)
on the left side of a pipe, so these tests collect the unsorted output from the
binary and sort it in Python instead.
"""


def test_assoc_set_and_get(run_bash):
    src = (
        'declare -A m\n'
        'm["host"]="example.com"\n'
        'echo ${m["host"]}\n'
    )
    r = run_bash(src)
    assert r.stdout == "example.com\n"


def test_assoc_get_with_var_key(run_bash):
    src = (
        'declare -A m\n'
        'm["port"]="8080"\n'
        'k=port\n'
        'echo ${m[$k]}\n'
    )
    r = run_bash(src)
    assert r.stdout == "8080\n"


def test_assoc_entry_count(run_bash):
    src = (
        'declare -A m\n'
        'm["a"]=1\n'
        'm["b"]=2\n'
        'm["c"]=3\n'
        'echo ${#m[@]}\n'
    )
    r = run_bash(src)
    assert r.stdout == "3\n"


def test_assoc_key_iteration(run_bash):
    src = (
        'declare -A m\n'
        'm["x"]=1\n'
        'm["y"]=2\n'
        'for k in "${!m[@]}"; do echo $k; done\n'
    )
    r = run_bash(src)
    # Iteration order is unspecified; sort the keys in Python.
    assert sorted(r.stdout.split()) == ["x", "y"]


def test_assoc_overwrite_key(run_bash):
    src = (
        'declare -A m\n'
        'm["k"]=first\n'
        'm["k"]=second\n'
        'echo ${m["k"]}\n'
        'echo ${#m[@]}\n'
    )
    r = run_bash(src)
    assert r.stdout == "second\n1\n"


def test_assoc_value_iteration(run_bash):
    src = (
        'declare -A m\n'
        'm["a"]=apple\n'
        'm["b"]=banana\n'
        'for k in "${!m[@]}"; do echo "${m[$k]}"; done\n'
    )
    r = run_bash(src)
    # Values come out in key-hash order; sort in Python.
    assert sorted(r.stdout.split()) == ["apple", "banana"]