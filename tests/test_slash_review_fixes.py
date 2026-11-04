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


# ===== second review round (2026-11-04) =====================================
@pytest.mark.parametrize("said", [
    "show me the docker commands", "give me the git commands",
    "tell me the keyboard commands", "slash prices now", "/etc/hosts",
])
def test_ordinary_requests_are_not_hijacked_by_the_slash_lane(said):
    assert sb.parse(said) is None


@pytest.mark.parametrize("said", ["list slash commands", "show commands", "slash commands", "slash"])
def test_real_list_requests_still_open_the_list(said):
    assert sb.parse(said)["kind"] == "list"


def test_speech_cut_never_lands_mid_word():
    out = speech_text.spoken_form("word " * 500, limit=100)
    assert out.endswith("…") and not out[:-1].endswith("wor")


def _runner():
    import tahuti_goal_runner as runner
    return runner


def test_menu_choice_that_is_gated_goes_through_the_approval_card(monkeypatch):
    runner = _runner()
    gated, ran = [], []

    class Lock:
        def __enter__(self): return True
        def __exit__(self, *a): return False

    monkeypatch.setattr(runner.sensei_slash, "sensei_lock", lambda: Lock())
    monkeypatch.setenv("DISPLAY", ":0")

    def fake_card(send, menu="", guide=False):
        if menu:                       # first call: the menu, person picked a gated command
            return {"ok": True, "mode": "menu", "action": "needs_gate", "send": "/git commit"}
        ran.append(send)               # second call: the command itself
        return {"ok": True, "mode": "output", "output": "done", "digest": "ok", "spoken": True}

    monkeypatch.setattr(runner, "_run_slash_card", fake_card)
    monkeypatch.setattr(runner, "_gate_desktop_action",
                        lambda gid, pattern, desc, denied: gated.append(pattern) or None)
    monkeypatch.setattr(runner, "_write_to_agent_thread", lambda *a, **k: None)
    runner._process_slash("g1", {"kind": "menu", "base": "git"})
    assert gated == ["tahuti:slash:git"] and ran == ["/git commit"]


def test_menu_choice_runs_nothing_when_the_gate_denies(monkeypatch):
    runner = _runner()
    ran = []

    class Lock:
        def __enter__(self): return True
        def __exit__(self, *a): return False

    monkeypatch.setattr(runner.sensei_slash, "sensei_lock", lambda: Lock())
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(runner, "_run_slash_card",
                        lambda send, menu="", guide=False: {"ok": True, "action": "needs_gate", "send": "/hooks disable"}
                        if menu else ran.append(send) or {})
    monkeypatch.setattr(runner, "_gate_desktop_action", lambda *a, **k: {"error": "denied"})
    text, _ = runner._process_slash("g1", {"kind": "menu", "base": "hooks"})
    assert ran == [] and "denied" in text


def test_busy_sensei_lock_means_nothing_is_typed(monkeypatch):
    runner = _runner()

    class Busy:
        def __enter__(self): return False
        def __exit__(self, *a): return False

    monkeypatch.setattr(runner.sensei_slash, "sensei_lock", lambda: Busy())
    monkeypatch.setattr(runner, "_run_slash_card", lambda *a, **k: pytest.fail("must not run"))
    monkeypatch.setattr(runner.sensei_slash, "ensure_sensei", lambda: pytest.fail("must not touch Sensei"))
    text, _ = runner._process_slash("g1", {"kind": "run", "base": "mode", "send": "/mode", "gated": False})
    assert "busy" in text.lower()


def test_private_tools_are_withheld_from_an_untrusted_provider(monkeypatch, tmp_path):
    runner = _runner()
    monkeypatch.setattr(runner, "_TRUSTED_PROVIDERS_FILE", tmp_path / "trusted")
    assert runner._trusted_providers() == {"local", "ollama"}
    (tmp_path / "trusted").write_text("# mine\nollama-cloud\n")
    assert "ollama-cloud" in runner._trusted_providers()


def test_launcher_uses_the_plain_command_when_systemd_run_does_not_work(monkeypatch):
    runner = _runner()
    monkeypatch.setitem(runner._SYSTEMD_RUN_OK, "at", runner.time.time())
    monkeypatch.setitem(runner._SYSTEMD_RUN_OK, "ok", False)
    assert runner._detached(["xdg-open", "x"]) == ["xdg-open", "x"]
    monkeypatch.setitem(runner._SYSTEMD_RUN_OK, "ok", True)
    assert runner._detached(["xdg-open", "x"])[:3] == ["systemd-run", "--user", "--scope"]


def test_read_page_ignores_proxy_environment(monkeypatch):
    import httpx
    runner = _runner()
    seen = {}

    class Boom(Exception):
        pass

    def fake_client(*a, **k):
        seen.update(k)
        raise Boom()

    monkeypatch.setattr(runner, "_resolve_public_ip", lambda h, p: "93.184.216.34")
    monkeypatch.setattr(httpx, "Client", fake_client)
    runner._read_page_tool("https://example.com")
    assert seen.get("trust_env") is False
