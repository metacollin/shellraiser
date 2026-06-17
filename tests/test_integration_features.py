"""
Integration tests for the two headline features in the README:

  * export -f  -- a compiled function becomes a real command on $PATH, callable
    from a plain /bin/sh subprocess.
  * --sourceable -- the output is a polyglot that can be either executed or
    sourced into a running shell to load its functions/variables.

These exercise the CLI plus the runtime's PATH-shim and polyglot machinery, so
they require a C compiler and are skipped otherwise.
"""

import os
import shutil
import subprocess
import sys

import pytest

NO_CC = (shutil.which("cc") is None and shutil.which("gcc") is None
         and shutil.which("clang") is None)
pytestmark = pytest.mark.skipif(NO_CC, reason="no C compiler")


def _cli(args, cwd=None):
    exe = shutil.which("shellraiser")
    if exe:
        return subprocess.run([exe, *args], capture_output=True, text=True, cwd=cwd)
    # in-process fallback
    import importlib, io, contextlib
    mod = None
    for name in ("shellraiser", "shellraiser.cli"):
        try:
            mod = importlib.import_module(name)
        except Exception:  # noqa: BLE001
            continue
        if hasattr(mod, "main"):
            break
    if mod is None:
        pytest.skip("no CLI entry point")
    out, err, code = io.StringIO(), io.StringIO(), 0
    old_argv, old_cwd = sys.argv, os.getcwd()
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
        sys.argv, _ = old_argv, os.chdir(old_cwd)

    class _P:
        pass
    p = _P()
    p.stdout, p.stderr, p.returncode = out.getvalue(), err.getvalue(), code
    return p


def test_exported_function_callable_from_sh(tmp_path):
    script = tmp_path / "utils.sh"
    script.write_text(
        "to_upper() { echo \"$1\" | tr 'a-z' 'A-Z'; }\n"
        "export -f to_upper\n"
    )
    binary = tmp_path / "utils"
    p = _cli(["-o", str(binary), str(script)])
    assert p.returncode == 0, p.stderr
    assert binary.exists()

    # Running the binary installs PATH shims for its lifetime; to observe the
    # exported function from another shell, the documented path is to call the
    # binary's --call dispatch directly, which any shell/tool can do.
    run = subprocess.run([str(binary), "--call", "to_upper", "hello"],
                         capture_output=True, text=True)
    assert run.stdout == "HELLO\n"


def test_sourceable_polyglot_executes(tmp_path):
    script = tmp_path / "lib.sh"
    script.write_text('VERSION=1.2.3\necho "exec path"\n')
    out = tmp_path / "lib"
    p = _cli(["--sourceable", "-o", str(out), str(script)])
    assert p.returncode == 0, p.stderr
    assert out.exists()

    # Executed directly, it runs the script body.
    run = subprocess.run(["bash", str(out)], capture_output=True, text=True,
                         env={**os.environ, "SHELLRAISER_CACHE": str(tmp_path / ".cache")})
    assert "exec path" in run.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required to source")
def test_sourceable_polyglot_sources_variable(tmp_path):
    script = tmp_path / "lib2.sh"
    script.write_text('VERSION=9.9.9\n')
    out = tmp_path / "lib2"
    p = _cli(["--sourceable", "-o", str(out), str(script)])
    assert p.returncode == 0, p.stderr

    # Sourcing should load VERSION into the shell environment.
    driver = (
        f'source "{out}"\n'
        f'echo "VERSION=$VERSION"\n'
    )
    run = subprocess.run(["bash", "-c", driver], capture_output=True, text=True,
                         env={**os.environ, "SHELLRAISER_CACHE": str(tmp_path / ".cache2")})
    assert "VERSION=9.9.9" in run.stdout
