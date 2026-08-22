"""Tests for ai_controller_paths.py.

These tests import but do not modify core input/profile scripts.
"""

import os
import sys
from pathlib import Path

# Make scripts/ importable without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from ai_controller_paths import ai_controller_dir


def test_ai_controller_dir_respects_env():
    """If AI_CONTROLLER_DIR is set, ai_controller_dir() returns it."""
    expected = "/tmp/fake-ai-controller"
    old = os.environ.get("AI_CONTROLLER_DIR")
    try:
        os.environ["AI_CONTROLLER_DIR"] = expected
        assert ai_controller_dir() == expected
    finally:
        if old is None:
            os.environ.pop("AI_CONTROLLER_DIR", None)
        else:
            os.environ["AI_CONTROLLER_DIR"] = old


def test_ai_controller_dir_falls_back_to_parent():
    """Without env var, ai_controller_dir() resolves to the repo root."""
    path = ai_controller_dir()
    assert os.path.isdir(path)
    assert Path(path).resolve().name == "ai-controller"
