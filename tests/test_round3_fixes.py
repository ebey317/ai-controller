"""Round-3 review tests (2026-11-04) — one test per [T] finding.

Covers: handoff_watch watch-window + session rules (findings 1, 2, 10),
goal-runner truthful `acted` + untrusted-pin history (5, 6), carrier-IP
handling (7), systemd probe failure TTL (8), guided-in_voice slash-parse
first-word rule (9), and the scoped Sensei-process check (4).
No live aoe/tmux/network: every subprocess is stubbed or rendered inert.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import handoff_watch as hw  # noqa: E402
import sensei_slash  # noqa: E402
import tahuti_goal_runner as runner  # noqa: E402

# ── Finding 1: explicit watch window 19:00–23:59 + 00:00–03:59 ────────────────


@pytest.mark.parametrize(
    "hh,mm,want",
    [
        (18, 59, False),
        (19, 0, True),
        (20, 30, True),
        (23, 59, True),
        (0, 0, True),
        (3, 59, True),
        (4, 0, False),
        (4, 6, False),
        (17, 0, False),
    ],
)
def test_watch_window_is_explicit(hh, mm, want):
    assert hw.in_watch_window(hh, mm) is want


# ── Finding 2: find_session guards + waiting-state rule ──────────────────────


def test_find_session_never_matches_empty_or_ellipsis_title():
    for bad in ("…", "", "….", "a"):
        live = {"abc12345": {"title": bad, "state": "idle"}}
        assert hw.find_session("gpt model.", live) is None, bad


def test_find_session_still_matches_real_titles():
    live = {
        "6e3cd115": {"title": "gpt model.", "state": "idle"},
        "def67890": {"title": "Learning-Style Wiring", "state": "running"},
    }
    assert hw.find_session("6e3cd115 gpt model.", live)[0] == "6e3cd115"
    assert hw.find_session("Learning-Style Wiring", live)[0] == "def67890"


def test_sessions_swallows_missing_aoe(monkeypatch):
    def boom(args, **kw):
        raise FileNotFoundError("aoe not on PATH")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(hw, "log", lambda msg: None)
    assert hw.sessions() == {}


@pytest.mark.parametrize(
    "state,busy",
    [
        ("running", True),
        ("waiting", True),  # blocked on an approval prompt — would answer it
        ("idle", False),
    ],
)
def test_waiting_sessions_are_left_alone(state, busy):
    assert hw.is_busy({"state": state, "title": "x"}) is busy


# ── Finding 10: send_message → send_to_chat; only success may record ─────────


def test_send_message_ok_when_submitted(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "send_to_chat",
        type("M", (), {"send": staticmethod(lambda sid, msg: "sent and submitted")}),
    )
    ok, detail = hw.send_message("abc12345", "hi")
    assert ok and "submitted" in detail


def test_send_message_failure_is_not_ok(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "send_to_chat",
        type(
            "M",
            (),
            {"send": staticmethod(lambda sid, msg: "send failed rc=1: no chat")},
        ),
    )
    ok, detail = hw.send_message("abc12345", "hi")
    assert not ok and detail.startswith("send failed")


def test_send_message_wraps_oserror(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "send_to_chat",
        type(
            "M",
            (),
            {
                "send": staticmethod(
                    lambda sid, msg: (_ for _ in ()).throw(OSError("no aoe"))
                )
            },
        ),
    )
    ok, detail = hw.send_message("abc12345", "hi")
    assert not ok and "OSError" in detail


# ── Finding 5: untrusted pins get NO prior conversation history ──────────────


@pytest.fixture()
def _pinned_run_capture(monkeypatch):
    """Stub sensei_model.run to record exactly which history list it got."""
    seen: list = []
    monkeypatch.setattr(
        runner.sensei_model,
        "run",
        lambda pin, system, hist, transcript, tools, execute=None: (
            seen.append(list(hist)),
            "answer",
        )[1],
    )
    return seen


def test_untrusted_pin_gets_no_history(monkeypatch, _pinned_run_capture):
    monkeypatch.setattr(runner, "_trusted_providers", lambda: {"local"})
    monkeypatch.setattr(
        runner,
        "_load_conversation_history",
        lambda: [{"role": "assistant", "content": "your private files…"}],
    )
    runner._call_pinned_model(("cloudy", "cloudy/big-model"), "what is 2+2", "g1", 0.0)
    assert (
        not _pinned_run_capture or _pinned_run_capture[0] == []
    ), "untrusted pin must not receive prior history"


def test_trusted_pin_keeps_history(monkeypatch, _pinned_run_capture):
    monkeypatch.setattr(runner, "_trusted_providers", lambda: {"local"})
    monkeypatch.setattr(
        runner,
        "_load_conversation_history",
        lambda: [{"role": "assistant", "content": "seen on a local turn"}],
    )
    runner._call_pinned_model(("local", "local/m"), "hi", "g1", 0.0)
    assert _pinned_run_capture and _pinned_run_capture[0]


# ── Finding 6: denied/failed tool calls must not count as acted ──────────────

_CALLSEQ = {"sequence": []}


def _fake_model_runner(execute_calls, then):
    def run(pin, system, hist, transcript, tools, execute=None):
        for call in execute_calls:
            execute(call)
        if then == "raise":
            raise RuntimeError("model died mid-turn")
        return "done"

    return run


@pytest.fixture(autouse=True)
def _reset_probe_cache():
    runner._SYSTEMD_RUN_OK.update(at=0.0, ok=False)
    yield
    runner._SYSTEMD_RUN_OK.update(at=0.0, ok=False)


def test_denied_call_does_not_report_actually_acting(monkeypatch, _pinned_run_capture):
    """A write_file that the gate DENIES (ok=False) must not count as acted:
    if the model then dies, the failure must count as a clean failure (falling
    back is allowed), NOT the 'did X then stopped' partial-failure message."""
    monkeypatch.setattr(runner, "_trusted_providers", lambda: {"local"})
    monkeypatch.setattr(
        runner.sensei_model,
        "run",
        _fake_model_runner(
            [{"name": "write_file", "arguments": {"path": "x", "content": "y"}}],
            then="raise",
        ),
    )
    monkeypatch.setattr(
        runner,
        "_execute_file_tool",
        lambda goal_id, call, allowed: {"ok": False, "error": "denied by gate"},
    )
    with pytest.raises(RuntimeError):
        runner._call_pinned_model(("local", "local/m"), "write it", "g1", 0.0)


def test_completed_call_still_counts_as_acting(monkeypatch, _pinned_run_capture):
    """The same death AFTER a write_file that actually went through (ok=True)
    DOES raise PinnedPartialFailure — the message 'did write_file but stopped'
    stays truthful."""
    monkeypatch.setattr(runner, "_trusted_providers", lambda: {"local"})
    monkeypatch.setattr(
        runner.sensei_model,
        "run",
        _fake_model_runner(
            [{"name": "write_file", "arguments": {"path": "x", "content": "y"}}],
            then="raise",
        ),
    )
    monkeypatch.setattr(
        runner,
        "_execute_file_tool",
        lambda goal_id, call, allowed: {"ok": True, "output": "wrote"},
    )
    with pytest.raises(runner.PinnedPartialFailure):
        runner._call_pinned_model(("local", "local/m"), "write it", "g1", 0.0)


# ── Finding 7: carrier-address unwrapping in _resolve_public_ip ──────────────
# Round-5 review #2/#3 took over this logic: the SAFETY check unwraps, the
# RETURN pins the ORIGINAL address, and NAT64 is the /96 network only. The
# mirror test below drove the real function with a fake getaddrinfo — so it
# tests the real path and its expectations are the real contract.


@pytest.mark.parametrize(
    "addr,want",
    [
        ("::ffff:127.0.0.1", None),
        ("::ffff:808:808", "::ffff:808:808"),  # == ::ffff:8.8.8.8
        ("64:ff9b::7f00:1", None),
        ("64:ff9b::808:808", "64:ff9b::808:808"),
        ("100.64.1.1", None),
        ("224.0.0.1", None),
        ("ff02::1", None),
        ("8.8.8.8", "8.8.8.8"),
    ],
)
def test_ip_unwrapping_rule(monkeypatch, addr, want):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, port, type=0: [(2, 1, 6, "", (addr, port))]
    )
    assert runner._resolve_public_ip("evil.example.test", 443) == want, addr


def test_resolve_public_ip_rejects_mapped_loopback_directly(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, type=0: [(2, 1, 6, "", ("::ffff:127.0.0.1", 443))],
    )
    assert runner._resolve_public_ip("evil.example.test", 443) is None


# ── Finding 8: failed probe cached only ~5 s, guarded by lock ────────────────


def test_systemd_probe_failure_retries_soon(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(runner.shutil, "which", lambda name: "/usr/bin/systemd-run")

    def fake_probe(args, **kw):
        calls["n"] += 1
        return subprocess.CompletedProcess(args, 1)  # probe FAILS

    monkeypatch.setattr(runner.subprocess, "run", fake_probe)

    clock = {"t": time.time()}
    monkeypatch.setattr(runner.time, "time", lambda: clock["t"])

    runner._SYSTEMD_RUN_OK.update(
        at=0.0, ok=False
    )  # stale cache, real 'now' is past TTL
    assert runner._systemd_run_works() is False  # probe #1
    assert calls["n"] == 1
    clock["t"] += 2  # inside the 5 s window — cached
    assert runner._systemd_run_works() is False
    assert calls["n"] == 1
    clock["t"] += 4  # past 5 s — re-probe
    assert runner._systemd_run_works() is False
    assert calls["n"] == 2


def test_systemd_probe_success_still_cached(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(runner.shutil, "which", lambda name: "/usr/bin/systemd-run")

    def fake_probe(args, **kw):
        calls["n"] += 1
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(runner.subprocess, "run", fake_probe)

    clock = {"t": time.time()}
    monkeypatch.setattr(runner.time, "time", lambda: clock["t"])

    runner._SYSTEMD_RUN_OK.update(at=0.0, ok=False)
    assert runner._systemd_run_works() is True  # probe #1 succeeds
    clock["t"] += 30
    assert runner._systemd_run_works() is True
    assert calls["n"] == 1  # success caches 60 s


# ── Finding 9: first-word rule in parse ──────────────────────────────────────


@pytest.mark.parametrize(
    "text,want",
    [
        ("slash git comit message", "unknown"),  # 4 words, first word IS a command
        (
            "slash /compact",
            "run",
        ),  # spoken with literal slash → normalised to the real command
        ("slash /etc/hosts", None),  # dictated path — not a command
        ("slash the budget for today", None),  # sentence — model answers
    ],
)
def test_parse_first_word_rule(text, want):
    got = sensei_slash.parse(text)
    if want is None:
        assert got is None, f"{text!r}: expected None, got {got}"
    elif want == "run":
        assert got is not None and got["kind"] == "run", f"{text!r}: got {got}"
        assert got["send"].startswith(
            "/"
        ), f"{text!r}: run send must go to Sensei as a command"
    else:
        assert got is not None and got["kind"] == want, f"{text!r}: got {got}"
        if want == "unknown":
            assert got.get("suggest"), f"{text!r}: unknown must carry suggestions"


# ── Finding 4: scoped Sensei-process check ──────────────────────────────────


def test_sensei_process_exists_needs_our_session(monkeypatch):
    monkeypatch.setattr(sensei_slash, "session_name", lambda: "tahuti")
    monkeypatch.setattr(
        sensei_slash, "_tmux", lambda *args, **kw: subprocess.CompletedProcess(args, 1)
    )
    assert sensei_slash._sensei_process_exists() is False
    assert sensei_slash._sensei_process_exists("some-missing-session") is False
