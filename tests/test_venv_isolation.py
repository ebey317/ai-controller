"""Guard the canonical venv against a leaked PYTHONPATH.

Hermes Agent and some parent shells inject PYTHONPATH pointing at a
different Python version's site-packages. That breaks the canonical
.venv/bin/python because it imports packages compiled for the wrong
interpreter. The run-tests wrapper must start pytest with PYTHONPATH
removed.
"""

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WRAPPER = REPO / "scripts" / "run-tests.sh"


def test_run_tests_wrapper_exists_and_is_executable():
    """The canonical test runner wrapper exists and is executable."""
    assert WRAPPER.exists(), f"missing wrapper: {WRAPPER}"
    assert os.access(WRAPPER, os.X_OK), f"wrapper is not executable: {WRAPPER}"


def test_run_tests_wrapper_collects_despite_leaked_pythonpath(tmp_path):
    """A leaked external PYTHONPATH must not break pytest collection."""
    bogus = tmp_path / "bogus"
    bogus.mkdir()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(bogus)

    result = subprocess.run(
        [str(WRAPPER), "--collect-only", "tests/test_utils.py"],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"wrapper failed with leaked PYTHONPATH:\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "2 tests collected" in result.stdout
    assert "error" not in result.stderr.lower()


def test_canonical_python_imports_fastapi_when_pythonpath_unset():
    """The canonical venv python can import core packages with no PYTHONPATH."""
    venv_python = REPO / ".venv" / "bin" / "python3"
    result = subprocess.run(
        [str(venv_python), "-c", "import fastapi, pydantic_core"],
        cwd=str(REPO),
        env={"PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"canonical python failed to import fastapi/pydantic_core:\n"
        f"stderr:\n{result.stderr}"
    )
