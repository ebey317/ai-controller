"""Regression tests for the 2026-11-04 code-review findings (slash/voice work).

Pure logic only: no tmux, no network, no real browser tabs."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import sensei_slash as sb  # noqa: E402
import speech_text  # noqa: E402


# -- parser ----------------------------------------------------------------
@pytest.mark.parametrize("said", [
    "slash the budget by ten percent",
    "slash and burn that draft",
    "slash go to the store",
])
def test_sentences_starting_with_slash_are_not_commands(said):
    assert sb.parse(said) is None          # falls through to the model


def test_go_alone_is_still_a_gated_command():
    r = sb.parse("slash go")
    assert r["kind"] == "run" and r["send"] == "/go" and r["gated"] is True


def test_command_that_takes_input_keeps_its_argument():
    assert sb.parse("slash remember: I like tea")["send"] == "/remember: I like tea"
    assert sb.parse("slash git commit fix typo")["send"] == "/git commit fix typo"


def test_bare_number_after_a_scroll_command():
    assert sb.parse("slash down 3")["send"] == "/down 3"


def test_turn_reminders_without_a_direction_does_not_switch_it_on():
    assert sb.parse("slash turn reminders")["on"] is None
    assert sb.parse("slash reminders off")["on"] is False
    assert sb.parse("slash reminders on")["on"] is True


def test_is_gated_survives_an_empty_name():
    assert sb.is_gated(":") is False


# -- speech ----------------------------------------------------------------
def test_identifiers_and_entities_survive_spoken_form():
    out = speech_text.spoken_form("Edit tahuti_goal_runner.py. I scored 5 &gt; 3.")
    assert "tahuti_goal_runner.py" in out
    assert "5 > 3" in out


def test_markdown_noise_is_still_removed():
    out = speech_text.spoken_form("## Plan\n**Bold** and *italic*\n> quoted\n* item")
    assert "#" not in out and "*" not in out and ">" not in out


def test_spoken_form_is_idempotent():
    once = speech_text.spoken_form("See `__init__.py` and a_b_c now.")
    assert speech_text.spoken_form(once) == once


# -- Sensei driver never types into a screen it cannot read ------------------
def test_ensure_idle_types_nothing_while_sensei_is_not_ready(monkeypatch):
    sent = []
    monkeypatch.setattr(sb, "_capture", lambda pane: "starting up...\n")
    monkeypatch.setattr(sb, "is_ready", lambda pane: False)
    monkeypatch.setattr(sb, "_tmux", lambda *a, **k: sent.append(a))
    assert sb.ensure_idle("%0", patience_s=0.2) is False
    assert sent == []


def test_ensure_idle_never_answers_a_waiting_question(monkeypatch):
    sent = []
    monkeypatch.setattr(sb, "_capture", lambda pane: "x")
    monkeypatch.setattr(sb, "modal_state", lambda scr: "prompt")
    monkeypatch.setattr(sb, "_tmux", lambda *a, **k: sent.append(a))
    assert sb.ensure_idle("%0", patience_s=0.2) is False
    assert sent == []


def test_ensure_sensei_does_not_restart_a_live_sensei(monkeypatch):
    sent = []
    monkeypatch.setattr(sb, "session_name", lambda: "master-ai")
    monkeypatch.setattr(sb, "sensei_pane", lambda sess: None)       # lookup keeps missing
    monkeypatch.setattr(sb, "_sensei_process_exists", lambda: True)  # but the process is there
    monkeypatch.setattr(sb.time, "sleep", lambda s: None)
    monkeypatch.setattr(sb, "_tmux", lambda *a, **k: sent.append(a) or
                        type("R", (), {"returncode": 0, "stdout": ""})())
    pane, started = sb.ensure_sensei(timeout=1)
    assert pane is None and started is False
    assert not any("C-c" in a for a in sent)


# -- runner ----------------------------------------------------------------
def test_pinned_failure_after_a_side_effect_is_not_retried_on_muse(monkeypatch):
    import tahuti_goal_runner as runner

    monkeypatch.setattr(runner.sensei_model, "active_pin", lambda: ("ollama-cloud", "kimi-x"))

    def partial(*a, **k):
        raise runner.PinnedPartialFailure(["open_url"], RuntimeError("HTTP 500"))

    monkeypatch.setattr(runner, "_call_pinned_model", partial)
    monkeypatch.setattr(runner, "_call_model_muse",
                        lambda *a, **k: pytest.fail("must not repeat the side effect on Muse"))
    text, model, _e, _s, route = runner._call_model("open youtube", "g1")
    assert "open url" in text and "did not run it again" in text
    assert route == "sensei-pinned-partial"


def test_read_page_refuses_when_any_address_is_private(monkeypatch):
    import socket

    import tahuti_goal_runner as runner

    def fake(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    assert runner._resolve_public_ip("mixed.test", 80) is None
