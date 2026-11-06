"""whats_new.py: the keyboard's update pin only reads, and compares versions right."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import whats_new as w  # noqa: E402


def test_version_compare_is_numeric_not_text():
    assert w._vkey("0.23.0") > w._vkey("0.22.7") > w._vkey("0.9.9")


def test_hermes_date_tags_are_not_releases():
    import re

    pat = [t for t in w.TOOLS if t[0] == "Hermes"][0][3]
    assert re.match(pat, "v0.23.0") and not re.match(pat, "v2026.9.24")


def test_commit_subjects_read_plain():
    assert w.humanize("fix(ui): card stays open") == "Fixed: Card stays open"
    assert w.humanize("sensei: add thing") == "Sensei: add thing"


def test_peek_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(w, "STATE", tmp_path / "seen.json")
    monkeypatch.setattr(w, "master_ai_section", lambda s: (["  x"], "abc"))
    monkeypatch.setattr(w, "tool_section", lambda *a: (["  y"], "1.0.0"))
    assert w.main(["--peek"]) == 0
    assert not (tmp_path / "seen.json").exists()
    assert w.main([]) == 0
    assert (tmp_path / "seen.json").exists()


def test_pin_never_runs_the_old_restarting_updater():
    src = (
        Path(__file__).resolve().parent.parent / "scripts" / "slide_keyboard.py"
    ).read_text()
    assert "update_master_ai.sh" not in src.split("DEFAULT_PINS")[1].split("]")[0]
