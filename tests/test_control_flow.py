"""
End-to-end: control flow. if/elif/else, while, until, for-in, C-style for,
case/esac with glob patterns, break, continue.
"""


def test_if_true_branch(run_bash):
    r = run_bash('if true; then echo yes; fi\n')
    assert r.stdout == "yes\n"


def test_if_false_branch_skipped(run_bash):
    r = run_bash('if false; then echo yes; fi\necho after\n')
    assert r.stdout == "after\n"


def test_if_else(run_bash):
    r = run_bash('if false; then echo a; else echo b; fi\n')
    assert r.stdout == "b\n"


def test_if_elif_else(run_bash):
    src = (
        'x=2\n'
        'if [ "$x" -eq 1 ]; then echo one\n'
        'elif [ "$x" -eq 2 ]; then echo two\n'
        'else echo other; fi\n'
    )
    r = run_bash(src)
    assert r.stdout == "two\n"


def test_while_loop_counts(run_bash):
    src = (
        'i=0\n'
        'while [ "$i" -lt 3 ]; do\n'
        '  echo $i\n'
        '  i=$(( i + 1 ))\n'
        'done\n'
    )
    r = run_bash(src)
    assert r.stdout == "0\n1\n2\n"


def test_until_loop(run_bash):
    src = (
        'i=0\n'
        'until [ "$i" -ge 2 ]; do\n'
        '  echo $i\n'
        '  i=$(( i + 1 ))\n'
        'done\n'
    )
    r = run_bash(src)
    assert r.stdout == "0\n1\n"


def test_for_in_list(run_bash):
    r = run_bash('for x in a b c; do echo $x; done\n')
    assert r.stdout == "a\nb\nc\n"


def test_for_in_word_splitting(run_bash):
    r = run_bash('items="p q r"\nfor x in $items; do echo $x; done\n')
    assert r.stdout == "p\nq\nr\n"


def test_c_style_for(run_bash):
    r = run_bash('for (( i=0; i<3; i++ )); do echo $i; done\n')
    assert r.stdout == "0\n1\n2\n"


def test_case_literal_match(run_bash):
    r = run_bash('x=hello\ncase $x in hello) echo hi;; *) echo no;; esac\n')
    assert r.stdout == "hi\n"


def test_case_glob_match(run_bash):
    r = run_bash('f=report.txt\ncase $f in *.txt) echo text;; *) echo other;; esac\n')
    assert r.stdout == "text\n"


def test_case_default(run_bash):
    r = run_bash('x=zzz\ncase $x in a) echo a;; *) echo default;; esac\n')
    assert r.stdout == "default\n"


def test_case_alternation(run_bash):
    r = run_bash('x=b\ncase $x in a|b|c) echo abc;; *) echo no;; esac\n')
    assert r.stdout == "abc\n"


def test_break_exits_loop(run_bash):
    src = (
        'for i in 1 2 3 4 5; do\n'
        '  if [ "$i" -eq 3 ]; then break; fi\n'
        '  echo $i\n'
        'done\n'
    )
    r = run_bash(src)
    assert r.stdout == "1\n2\n"


def test_continue_skips_iteration(run_bash):
    src = (
        'for i in 1 2 3 4; do\n'
        '  if [ "$i" -eq 2 ]; then continue; fi\n'
        '  echo $i\n'
        'done\n'
    )
    r = run_bash(src)
    assert r.stdout == "1\n3\n4\n"


def test_nested_loops(run_bash):
    src = (
        'for i in 1 2; do\n'
        '  for j in a b; do\n'
        '    echo "$i$j"\n'
        '  done\n'
        'done\n'
    )
    r = run_bash(src)
    assert r.stdout == "1a\n1b\n2a\n2b\n"
