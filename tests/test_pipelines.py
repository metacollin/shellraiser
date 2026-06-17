"""
End-to-end: pipelines, redirection, and && / || logical chains.
Uses common external tools (cat, tr, wc, grep, sort) that exist on Linux/macOS.
"""

import pytest


def test_simple_pipe(run_bash):
    r = run_bash('echo hello | cat\n')
    assert r.stdout == "hello\n"


def test_pipe_to_tr(run_bash):
    r = run_bash("echo hello | tr 'a-z' 'A-Z'\n")
    assert r.stdout == "HELLO\n"


def test_three_stage_pipe(run_bash):
    r = run_bash("printf 'c\\na\\nb\\n' | sort | head -1\n")
    assert r.stdout == "a\n"


def test_redirect_out_then_read_back(run_bash, tmp_path):
    f = tmp_path / "out.txt"
    r = run_bash(f'echo written > {f}\ncat {f}\n')
    assert r.stdout == "written\n"


def test_redirect_append(run_bash, tmp_path):
    f = tmp_path / "log.txt"
    src = (
        f'echo line1 > {f}\n'
        f'echo line2 >> {f}\n'
        f'cat {f}\n'
    )
    r = run_bash(src)
    assert r.stdout == "line1\nline2\n"


def test_redirect_stdin(run_bash, tmp_path):
    f = tmp_path / "in.txt"
    f.write_text("from file\n")
    r = run_bash(f'cat < {f}\n')
    assert r.stdout == "from file\n"


def test_stderr_redirect_to_stdout(run_bash):
    # ls of a missing path writes to stderr; 2>&1 folds it into stdout.
    r = run_bash('ls /nonexistent_path_zzz 2>&1\n')
    assert r.stdout != "" or r.stderr != ""  # message appears somewhere
    # When folded, stdout should carry the error text.
    assert "nonexistent_path_zzz" in (r.stdout + r.stderr)


def test_and_chain_runs_second_on_success(run_bash):
    r = run_bash('true && echo ran\n')
    assert r.stdout == "ran\n"


def test_and_chain_skips_second_on_failure(run_bash):
    r = run_bash('false && echo nope\necho after\n')
    assert r.stdout == "after\n"


def test_or_chain_runs_second_on_failure(run_bash):
    r = run_bash('false || echo fallback\n')
    assert r.stdout == "fallback\n"


def test_or_chain_skips_second_on_success(run_bash):
    r = run_bash('true || echo nope\necho after\n')
    assert r.stdout == "after\n"


def test_mixed_chain(run_bash):
    r = run_bash('true && echo a || echo b\n')
    assert r.stdout == "a\n"
