"""B6 subset: Sensei modal recovery, without tmux, GUI, or real time."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import sensei_slash as slash  # noqa: E402


@pytest.mark.parametrize("state,expected", [
    ("picker", [("send-keys", "-t", "test-pane", "Escape")]),
    ("pager", [("send-keys", "-t", "test-pane", "C-u"),
               ("send-keys", "-t", "test-pane", "-l", "--", "q"),
               ("send-keys", "-t", "test-pane", "Enter")]),
    ("hub", [("send-keys", "-t", "test-pane", "C-u"),
             ("send-keys", "-t", "test-pane", "-l", "--", "q"),
             ("send-keys", "-t", "test-pane", "Enter")]),
])
def test_closes_only_detected_modal_then_proves_idle(monkeypatch, state, expected):
    states = iter([state, None])
    sent = []
    monkeypatch.setattr(slash, "_capture", lambda pane: "screen")
    monkeypatch.setattr(slash, "modal_state", lambda screen: next(states))
    monkeypatch.setattr(slash, "is_ready", lambda pane: True)
    monkeypatch.setattr(slash, "_tmux", lambda *args: sent.append(args))
    monkeypatch.setattr(slash, "time", SimpleNamespace(time=lambda: 1.0, sleep=lambda _: None))
    assert slash.ensure_idle("test-pane", patience_s=10) is True
    assert sent == expected


def test_pending_question_never_receives_keystrokes(monkeypatch):
    sent = []
    monkeypatch.setattr(slash, "_capture", lambda pane: "screen")
    monkeypatch.setattr(slash, "modal_state", lambda screen: "prompt")
    monkeypatch.setattr(slash, "_tmux", lambda *args: sent.append(args))
    monkeypatch.setattr(slash, "time", SimpleNamespace(time=lambda: 1.0, sleep=lambda _: None))
    assert slash.ensure_idle("test-pane", patience_s=10) is False
    assert sent == []


def test_no_ready_footer_expires_without_typing(monkeypatch):
    ticks = iter([0.0, 0.1, 0.5, 1.5])
    sent = []
    monkeypatch.setattr(slash, "_capture", lambda pane: "screen")
    monkeypatch.setattr(slash, "modal_state", lambda screen: None)
    monkeypatch.setattr(slash, "is_ready", lambda pane: False)
    monkeypatch.setattr(slash, "_tmux", lambda *args: sent.append(args))
    monkeypatch.setattr(slash, "time", SimpleNamespace(time=lambda: next(ticks), sleep=lambda _: None))
    assert slash.ensure_idle("test-pane", patience_s=1) is False
    assert sent == []


def test_modal_cleanup_failure_does_not_report_idle(monkeypatch):
    sent = []
    ticks = iter([0.0, 0.1, 1.7, 3.3])
    monkeypatch.setattr(slash, "_capture", lambda pane: "screen")
    monkeypatch.setattr(slash, "modal_state", lambda screen: "pager")
    monkeypatch.setattr(slash, "is_ready", lambda pane: True)
    monkeypatch.setattr(slash, "_tmux", lambda *args: sent.append(args))
    monkeypatch.setattr(slash, "time", SimpleNamespace(time=lambda: next(ticks), sleep=lambda _: None))
    assert slash.ensure_idle("test-pane", tries=1, patience_s=10) is False
    assert sent == [
        ("send-keys", "-t", "test-pane", "C-u"),
        ("send-keys", "-t", "test-pane", "-l", "--", "q"),
        ("send-keys", "-t", "test-pane", "Enter"),
    ]


def test_empty_answer_has_explicit_nonempty_spoken_fallback():
    assert slash.spoken_digest("", "doctor") == "Ran slash doctor in Sensei; it printed nothing."


def test_empty_answer_never_claims_result_content():
    digest = slash.spoken_digest("\n│ ── ╭╮\n", "doctor")
    assert digest == "Ran slash doctor in Sensei; it printed nothing."
