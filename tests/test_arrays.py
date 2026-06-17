"""
End-to-end: indexed arrays. List assignment, element access, @-expansion,
length, append, and initialization from command substitution.
"""

import pytest


def test_array_element_access(run_bash):
    r = run_bash('arr=(a b c)\necho ${arr[0]} ${arr[2]}\n')
    assert r.stdout == "a c\n"


def test_array_all_elements(run_bash):
    r = run_bash('arr=(x y z)\necho ${arr[@]}\n')
    assert r.stdout == "x y z\n"


def test_array_length(run_bash):
    r = run_bash('arr=(one two three four)\necho ${#arr[@]}\n')
    assert r.stdout == "4\n"


def test_array_append(run_bash):
    r = run_bash('arr=(a b)\narr+=(c)\necho ${arr[@]}\n')
    assert r.stdout == "a b c\n"


def test_array_append_multiple(run_bash):
    r = run_bash('arr=(a)\narr+=(b c d)\necho ${#arr[@]}\n')
    assert r.stdout == "4\n"


def test_array_index_assignment(run_bash):
    r = run_bash('arr=(a b c)\narr[1]=Z\necho ${arr[@]}\n')
    assert r.stdout == "a Z c\n"


def test_array_iteration(run_bash):
    src = (
        'arr=(red green blue)\n'
        'for c in "${arr[@]}"; do echo $c; done\n'
    )
    r = run_bash(src)
    assert r.stdout == "red\ngreen\nblue\n"


def test_array_from_command_substitution(run_bash):
    r = run_bash('arr=($(echo p q r))\necho ${#arr[@]}\n')
    assert r.stdout == "3\n"


def test_array_from_command_substitution_via_append(run_bash):
    # The supported way to get the same result today: capture once, then append
    # the (word-split) words. This exercises array-append + word splitting on a
    # command-substitution result without hitting the prefix-path bug above.
    src = (
        'out=$(echo p q r)\n'
        'arr=()\n'
        'for w in $out; do arr+=("$w"); done\n'
        'echo ${#arr[@]}\n'
    )
    r = run_bash(src)
    assert r.stdout == "3\n"


def test_array_star_expansion(run_bash):
    r = run_bash('arr=(1 2 3)\necho ${arr[*]}\n')
    assert r.stdout == "1 2 3\n"
