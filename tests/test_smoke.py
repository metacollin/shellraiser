"""
Smoke tests: the public API exists and transpile() emits well-formed C.
These run without a C compiler.
"""

import pytest


def test_module_exposes_transpile(sr):
    assert callable(sr.transpile)


def test_module_exposes_main(sr):
    # main() is the CLI entry point referenced by the console script.
    assert callable(getattr(sr, "main", None))


def test_parse_error_is_exception(sr):
    assert issubclass(sr.ParseError, Exception)


def test_token_type_enum_has_core_members(sr):
    TT = sr.TT
    for member in ("WORD", "PIPE", "IF", "FOR", "WHILE", "CASE", "FUNCTION", "EOF"):
        assert hasattr(TT, member), f"TT.{member} missing"


def test_empty_program_transpiles(transpile):
    c = transpile("")
    assert "int main(int argc, char **argv)" in c


def test_generated_c_has_runtime_include(transpile):
    c = transpile("echo hi\n")
    assert '#include "bash_runtime.h"' in c


def test_generated_c_initializes_runtime(transpile):
    # The generated main() must initialize the runtime before doing work.
    c = transpile("echo hi\n")
    assert "rt_init(" in c


@pytest.mark.parametrize("snippet", [
    "x=1\n",
    "echo hello world\n",
    "if true; then echo yes; fi\n",
    "for i in 1 2 3; do echo $i; done\n",
    "while false; do echo nope; done\n",
    "case $x in a) echo a;; *) echo other;; esac\n",
    "greet() { echo hi; }\ngreet\n",
    "arr=(a b c)\necho ${arr[@]}\n",
    "n=$(( 1 + 2 ))\necho $n\n",
    "a=1 && b=2 || c=3\n",
    "echo one | cat\n",
])
def test_representative_programs_transpile_without_error(transpile, snippet):
    c = transpile(snippet)
    assert "int main(int argc, char **argv)" in c
    assert len(c) > 0


def test_shebang_is_tolerated_by_transpile(transpile):
    # transpile() itself receives source; the CLI strips the shebang, but a
    # leading comment line must not break transpilation either way.
    c = transpile("# a comment\necho hi\n")
    assert "int main" in c
