"""
Shared pytest fixtures for the shellraiser test suite.

Two layers of testing are supported:

  1. Transpile-level (fast, no compiler): call `transpile(src)` and assert on
     the emitted C. Use the `transpile` fixture.

  2. End-to-end (requires a C compiler): compile a bash script to a native
     binary via the real runtime, run it, and assert on stdout / exit code.
     Use the `run_bash` fixture. These auto-skip when no compiler or no
     runtime is available, so the suite still passes on a machine without gcc.

Layout-agnostic: works whether shellraiser is a flat top-level module
(`shellraiser.py` exposing `transpile`/`main`) or the src package
(`shellraiser.cli`). The runtime is located via, in order:
  - $SHELLRAISER_RUNTIME (a directory containing bash_runtime.c/.h)
  - the installed `shellraiser.runtime` package (importlib.resources)
  - a `runtime/` directory next to the shellraiser module
"""

import importlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


# --------------------------------------------------------------------------
# Locate the shellraiser API regardless of repo layout.
# --------------------------------------------------------------------------
def _load_shellraiser_module():
    last_err = None
    for name in ("shellraiser", "shellraiser.cli"):
        try:
            mod = importlib.import_module(name)
        except Exception as e:  # noqa: BLE001 - we try the next candidate
            last_err = e
            continue
        if hasattr(mod, "transpile"):
            return mod
    raise ImportError(
        "Could not import shellraiser's transpile(). Tried 'shellraiser' and "
        "'shellraiser.cli'. Install the package (pip install -e .) or put the "
        "module on PYTHONPATH. Last error: %r" % (last_err,)
    )


SR = _load_shellraiser_module()


@pytest.fixture(scope="session")
def sr():
    """The imported shellraiser module (exposes transpile, Tokenizer, etc.)."""
    return SR


@pytest.fixture(scope="session")
def transpile():
    """The transpile(source:str)->str function under test."""
    return SR.transpile


# --------------------------------------------------------------------------
# Runtime + compiler discovery.
# --------------------------------------------------------------------------
def _find_runtime_dir():
    # 1. Explicit override.
    env = os.environ.get("SHELLRAISER_RUNTIME")
    if env and Path(env, "bash_runtime.c").is_file():
        return Path(env)

    # 2. Installed package data (Layout B).
    try:
        from importlib.resources import files
        pkg = files("shellraiser.runtime")
        if pkg.joinpath("bash_runtime.c").is_file():
            # May be inside a zip; copy to a real dir if needed.
            cand = Path(str(pkg))
            if (cand / "bash_runtime.c").is_file():
                return cand
    except Exception:  # noqa: BLE001
        pass

    # 3. runtime/ next to the shellraiser module (flat layout / source checkout).
    mod_file = getattr(SR, "__file__", None)
    if mod_file:
        cand = Path(mod_file).resolve().parent / "runtime"
        if (cand / "bash_runtime.c").is_file():
            return cand
        # src-layout source tree: src/shellraiser/runtime
        cand2 = Path(mod_file).resolve().parent / "runtime"
        if (cand2 / "bash_runtime.c").is_file():
            return cand2

    return None


def _find_compiler():
    if os.environ.get("CC"):
        resolved = shutil.which(os.environ["CC"])
        if resolved:
            return resolved
    for name in ("cc", "gcc", "clang"):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    return None


RUNTIME_DIR = _find_runtime_dir()
COMPILER = _find_compiler()


@pytest.fixture(scope="session")
def runtime_dir():
    if RUNTIME_DIR is None:
        pytest.skip("bash_runtime.c/.h not found (set SHELLRAISER_RUNTIME)")
    return RUNTIME_DIR


@pytest.fixture(scope="session")
def compiler():
    if COMPILER is None:
        pytest.skip("no C compiler found on PATH (set CC)")
    return COMPILER


# --------------------------------------------------------------------------
# The end-to-end harness: bash source -> C -> native binary -> run.
# --------------------------------------------------------------------------
class _Result:
    __slots__ = ("stdout", "stderr", "returncode")

    def __init__(self, stdout, stderr, returncode):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode

    def __repr__(self):
        return (f"_Result(returncode={self.returncode}, "
                f"stdout={self.stdout!r}, stderr={self.stderr!r})")


@pytest.fixture
def compile_bash(transpile, runtime_dir, compiler, tmp_path):
    """
    Returns a function: compile_bash(source, *, cflags=None) -> Path to binary.
    Raises AssertionError with the compiler diagnostics if compilation fails,
    so a codegen regression surfaces as a clear test failure.
    """
    rt_c = str(Path(runtime_dir) / "bash_runtime.c")

    def _compile(source: str, *, cflags=None, name="prog"):
        c_code = transpile(source)
        c_path = tmp_path / f"{name}.c"
        c_path.write_text(c_code)
        out_bin = tmp_path / name
        flags = cflags if cflags is not None else ["-O0", "-w"]
        cmd = [compiler, *flags, "-std=c11", f"-I{runtime_dir}",
               str(c_path), rt_c, "-o", str(out_bin)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        assert proc.returncode == 0, (
            f"compilation failed for source:\n{source}\n\n"
            f"command: {' '.join(cmd)}\n\nstderr:\n{proc.stderr}\n\n"
            f"generated C:\n{c_code}"
        )
        out_bin.chmod(0o755)
        return out_bin

    return _compile


@pytest.fixture
def run_bash(compile_bash):
    """
    Returns a function: run_bash(source, args=(), stdin=None, timeout=20) -> _Result.
    Compiles the bash source to a native binary and runs it.
    """
    def _run(source: str, args=(), stdin=None, timeout=20, cflags=None):
        binary = compile_bash(source, cflags=cflags)
        proc = subprocess.run(
            [str(binary), *map(str, args)],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return _Result(proc.stdout, proc.stderr, proc.returncode)

    return _run
