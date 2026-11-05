"""Regression tests for the shared voice goal schema and intent classifier."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import voice_goal_schema as vgs  # noqa: E402


def test_how_do_i_install_software_is_chat():
    assert vgs.classify_transcript("How do I install software?") == "chat"


def test_do_not_delete_anything_is_chat():
    assert vgs.classify_transcript("Do not delete anything") == "chat"


def test_task_verb_routes_to_task():
    assert vgs.classify_transcript("write a todo list") == "task"


def test_task_type_extracts_first_verb():
    assert vgs.task_type("write a todo list") == "write"


def test_build_goal_payload_includes_all_required_fields():
    payload = vgs.build_goal_payload("build a widget")
    for field in vgs.GOAL_REQUIRED_FIELDS:
        assert field in payload
    assert payload["transcript"] == "build a widget"
    assert payload["type"] == "build"
    assert payload["source"] == "voice_bridge"


if __name__ == "__main__":
    raise SystemExit(__import__("pytest").main([__file__, "-v"]))
