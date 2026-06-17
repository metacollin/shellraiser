"""
CLI tests. These drive shellraiser's command-line interface end to end.

The CLI is located as, in order:
  - the installed `shellraiser` console script on PATH
  - `python -m shellraiser` (if a __main__ is present)
  - invoking main() in-process with patched argv/sys.exit

--emit-only needs no compiler; the compile paths skip if none is present.
"""

import os
import shutil
import subprocess
import sys

import pytest


def _console_script():
    return shutil.which("shellraiser")


def _run_cli(args, cwd=None):
    """Run the CLI, preferring the installed console script."""
    exe = _console_script()
    if exe:
        return subprocess.run([exe, *args], capture_output=True, text=True, cwd=cwd)
    # Fall back to in-process invocation of main() with patched argv.
    import importlib
    mod = None
    for name in ("shellraiser", "shellraiser.cli"):
        try:
            mod = importlib.import_module(name)
        except Exception:  # noqa: BLE001
            continue
        if hasattr(mod, "main"):
            break
    if mod is None or not hasattr(mod, "main"):
        pytest.skip("no CLI entry point available")

    import io
    import contextlib

    old_argv = sys.argv
    out, err = io.StringIO(), io.StringIO()
    code = 0
    old_cwd = os.getcwd()
    try:
        if cwd:
            os.chdir(cwd)
        sys.argv = ["shellraiser", *args]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                mod.main()
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    finally:
        sys.argv = old_argv
        os.chdir(old_cwd)

    class _P:
        pass

    p = _P()
    p.stdout = out.getvalue()
    p.stderr = err.getvalue()
    p.returncode = code
    return p


def test_emit_only_prints_c(tmp_path):
    script = tmp_path / "s.sh"
    script.write_text("echo hi\n")
    p = _run_cli(["--emit-only", str(script)])
    assert p.returncode == 0
    assert '#include "bash_runtime.h"' in p.stdout
    assert "int main(int argc, char **argv)" in p.stdout


def test_emit_only_strips_shebang(tmp_path):
    script = tmp_path / "s.sh"
    script.write_text("#!/usr/bin/env bash\necho hi\n")
    p = _run_cli(["--emit-only", str(script)])
    assert p.returncode == 0
    # The shebang line must not appear in the generated C.
    assert "#!/usr/bin/env bash" not in p.stdout


def test_missing_input_file_errors(tmp_path):
    p = _run_cli([str(tmp_path / "does_not_exist.sh")])
    assert p.returncode != 0
    assert "not found" in (p.stdout + p.stderr).lower()


def test_parse_error_reported(tmp_path):
    script = tmp_path / "bad.sh"
    script.write_text("if true; then echo x\n")  # missing fi
    p = _run_cli([str(script)])
    assert p.returncode != 0
    assert "error" in (p.stdout + p.stderr).lower()


@pytest.mark.skipif(
    shutil.which("cc") is None and shutil.which("gcc") is None and shutil.which("clang") is None,
    reason="no C compiler",
)
def test_output_flag_produces_named_binary(tmp_path):
    script = tmp_path / "prog.sh"
    script.write_text('echo built\n')
    out_bin = tmp_path / "custom_name"
    p = _run_cli(["-o", str(out_bin), str(script)])
    assert p.returncode == 0, p.stderr
    assert out_bin.exists()
    run = subprocess.run([str(out_bin)], capture_output=True, text=True)
    assert run.stdout == "built\n"


@pytest.mark.skipif(
    shutil.which("cc") is None and shutil.which("gcc") is None and shutil.which("clang") is None,
    reason="no C compiler",
)
def test_save_source_keeps_c_file(tmp_path):
    script = tmp_path / "keep.sh"
    script.write_text('echo x\n')
    p = _run_cli(["--save-source", "-o", str(tmp_path / "keep"), str(script)])
    assert p.returncode == 0, p.stderr
    # The .c is written next to the script (per the documented behavior).
    assert (tmp_path / "keep.c").exists()
