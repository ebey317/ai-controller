"""Smoke tests for AI Controller utility helpers.

These tests avoid anything that depends on local-only files or hardware.
"""

import os
from pathlib import Path

import pytest


# Ensure scripts/ is importable for tests that need it.
@pytest.fixture(scope="session", autouse=True)
def add_scripts_to_path():
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    if str(scripts_dir) not in os.environ.get("PYTHONPATH", ""):
        os.environ["PYTHONPATH"] = f"{scripts_dir}:{os.environ.get('PYTHONPATH', '')}"


def test_repo_root_contains_scripts():
    """The repository has the expected scripts/ directory."""
    repo_root = Path(__file__).resolve().parent.parent
    assert (repo_root / "scripts").is_dir()


def test_version_file_exists():
    """The VERSION file exists at the repo root."""
    version_file = Path(__file__).resolve().parent.parent / "VERSION"
    assert version_file.exists()
    assert version_file.read_text().strip()
