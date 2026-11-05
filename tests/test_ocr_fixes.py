"""OCR round tests (2026-11-05) — findings A–H in ~/.tahuti/review/ocr_ai-controller.md.

[T]-marked: A (ptt take race), C (mcp_tabs positive evidence), D (to_url URIs),
E (verified avatar-pulse kill), F (presenter guide_card guard).
B/G/H are unmarked fixes but get cheap coverage where it is free.
"""

from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import mcp_tabs  # noqa: E402
import speech_text  # noqa: E402
import tahuti_goal_runner as runner  # noqa: E402

PTT_SRC = SCRIPTS / "ptt_pynput.py"
PRESENTER_SRC = SCRIPTS / "tahuti_choice_presenter.py"


# ── Finding A: the second take's "sending" caption must survive ──────────────

class _QuietLog:
    warning = staticmethod(lambda *a, **k: None)  # type: ignore[attr-defined]


@pytest.fixture()
def race_ns(tmp_path):
    """stop_and_send + caption IO, extracted from ptt_pynput's source.

    ptt_pynput is unsafe to import in a test process (singleton file lock,
    background listener threads — see tests/test_ptt_notify.py), so the race
    logic under test is pulled out of the module AST and run in a clean
    namespace against a temp caption file. `time.time()` is pinned to NOW
    (300.0) — in the module's real environment the caption writer and reader
    share one clock, so the fixture must too.

    Round-4 contract: `stop_and_send(was_agent_take, sending_time, take_id)`
    and the finally/no-flag-reset rules are additionally take-guarded via
    `_take_still_current(take_id)`; these unit tests run at take_seq=0 with
    take_id=0 (the current take). The OUT-OF-ORDER race itself is covered
    through the REAL on_release path in tests/test_round4_fixes.py, where
    the clock advances and stamps are unique.
    """
    capfile = tmp_path / "caption.json"
    tree = ast.parse(PTT_SRC.read_text(encoding="utf-8"))
    want = {"stop_and_send", "_should_clear_to_idle", "_write_caption",
            "_read_caption", "_take_still_current"}
    chunks = [ast.unparse(n) for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name in want]
    missing = want - {getattr(n, "name", "") for n in tree.body}
    assert not missing, f"extraction lost {missing}"
    ns: dict = {
        "CAPTION_FILE": str(capfile),
        "log": _QuietLog(),
        "json": json, "os": os,
        "time": type("_Clock", (), {"time": staticmethod(lambda: 300.0)}),
        # replaced per-test; the real impl is unimportable
        "_stop_and_send_impl": lambda was, take: None,
        "take_seq": 0,   # unit tests exercise the CURRENT take (id 0)
    }
    exec(compile("\n".join(chunks), str(PTT_SRC), "exec"), ns)  # noqa: S102
    return ns, capfile


def _destroys_launch():  # helper readability shim
    return None


def test_second_takes_sending_survives_first_takes_finally(race_ns):
    """The exact reported race: take #1's stop-thread finally runs AFTER
    take #2 already stamped the caption 'sending' at a later time (both
    fresh on the fixture clock). The old finally (any 'sending', no time
    check) rewrote it to 'idle' and left take #2's avatar on a dead state."""
    ns, capfile = race_ns
    T1, T2 = 100.0, 250.0   # clock is pinned at 300.0: both stamps are fresh
    capfile.write_text(json.dumps({"text": "", "state": "sending", "time": T2}))
    ns["stop_and_send"](True, T1, 0)   # take_id 0 == take_seq: current take
    data = json.loads(capfile.read_text())
    assert data["state"] == "sending" and data["time"] == T2, \
        "a NEWER take's sending stamp was clobbered to idle"


def test_own_stale_sending_is_still_cleared(race_ns):
    """No race: the caption still carries THIS release's stamp — the avatar
    must be released (the old bug going the other way: never 'idle' again)."""
    ns, capfile = race_ns
    T1 = 250.0   # fresh atNOW=300
    capfile.write_text(json.dumps({"text": "", "state": "sending", "time": T1}))
    ns["stop_and_send"](True, T1, 0)   # take_id 0 == take_seq: current take
    assert json.loads(capfile.read_text())["state"] == "idle"


def test_impl_receives_captured_take_not_reread_global(race_ns):
    """The impl must see the flag captured at RELEASE time (passed in), not
    read `_goal_take` from the module global inside the racing thread.
    (Round-4: it also receives `take_id` for the take-guard; seen here.)"""
    ns, _ = race_ns
    seen = []
    ns["_stop_and_send_impl"] = lambda was, take: seen.append(was)
    ns["stop_and_send"](True, 5.0, 0)
    ns["stop_and_send"](False, 0.0, 0)
    assert seen == [True, False]


def test_dictation_take_never_touches_the_avatar_caption(race_ns):
    ns, capfile = race_ns
    capfile.write_text(json.dumps({"text": "", "state": "sending", "time": 250.0}))
    ns["stop_and_send"](False, 0.0, 0)
    data = json.loads(capfile.read_text())
    assert data["state"] == "sending"   # plain dictation had no avatar


def test_should_clear_to_idle_pure_cases(race_ns):
    ns, _ = race_ns
    assert ns["_should_clear_to_idle"]({"state": "sending", "time": 5.0}, 5.0)
    assert not ns["_should_clear_to_idle"]({"state": "heard", "time": 5.0}, 5.0)
    assert not ns["_should_clear_to_idle"]({"state": "sending", "time": 6.0}, 5.0)
    assert not ns["_should_clear_to_idle"]({"state": "sending", "time": "gone"}, 5.0)
    assert not ns["_should_clear_to_idle"]({}, 0.0)


# ── Finding C: open_tab needs POSITIVE evidence of a created tab ─────────────

class _FakeBridge:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def _bridge_alive(self):
        return True

    def _dispatch(self, kind, payload, wait=None):
        self.calls.append((kind, payload))
        return self.reply


@pytest.fixture()
def _bridge(monkeypatch):
    holder: dict = {}

    def install(reply):
        fake = _FakeBridge(reply)
        monkeypatch.setattr(mcp_tabs, "_server", lambda: fake)
        holder["fake"] = fake
        return fake
    return install


def test_open_tab_ok_when_tab_created_anywhere_nested(_bridge, monkeypatch):
    _bridge({"ok": True, "result": {"result": {"result": {
        "ok": True, "tab_created": {"id": 7}}}}})
    out = mcp_tabs.open_tab("https://example.com")
    assert out["ok"] is True and out["detail"]["id"] == 7


def test_open_tab_rejects_ok_true_without_created_tab(_bridge):
    """The old false-positive: ok-trues all the way down with NO tab —
    success used to be inferred from the absence of inner.ok being False."""
    _bridge({"ok": True, "result": {"result": {"result": {"ok": True}}}})
    assert mcp_tabs.open_tab("https://example.com")["ok"] is False


def test_open_tab_rejects_json_rpc_error_and_odd_envelopes(_bridge):
    for reply in [{"error": {"code": -1, "message": "no extension"}},
                  None, "garbage string", {"result": {"result": {}}}]:
        _bridge(reply)
        assert mcp_tabs.open_tab("https://example.com")["ok"] is False, reply


def test_open_tab_list_nesting_tolerated(_bridge):
    _bridge({"result": {"rows": [1, {"note": "x"}, {"tab_created": {"id": 42}}]}})
    out = mcp_tabs.open_tab("/tmp/x.html")
    assert out["ok"] is True and out["detail"]["id"] == 42


def test_find_tab_created_requires_intable_id(race_ns_unused=None):
    assert mcp_tabs._find_tab_created({"tab_created": {"id": 5}}) == {"id": 5}
    assert mcp_tabs._find_tab_created({"tab_created": {"id": "9"}}) == {"id": "9"}
    assert mcp_tabs._find_tab_created({"tab_created": {"id": None}}) is None
    assert mcp_tabs._find_tab_created({"tab_created": {}}) is None
    assert mcp_tabs._find_tab_created({"error": True}) is None


# ── Finding D: to_url — URIs are not files; empty target is an error ─────────

@pytest.mark.parametrize("uri", [
    "mailto:you@example.com", "tel:911", "data:text/plain,hi",
    "magnet:?xt=urn:btih:x", "https://example.com/x?y=1", "file:///tmp/a.html",
])
def test_to_url_passes_schemes_through_untouched(uri):
    assert mcp_tabs.to_url(uri) == uri


def test_to_url_empty_target_is_an_error(monkeypatch):
    with pytest.raises(ValueError):
        mcp_tabs.to_url("")
    with pytest.raises(ValueError):
        mcp_tabs.to_url("   ")


def test_open_tab_empty_target_reports_ok_false_not_cwd(monkeypatch):
    """The old behaviour silently made file://<cwd> and OPENED the caller's
    whole directory. It must come back ok=False instead."""
    out = mcp_tabs.open_tab("")
    assert out["ok"] is False and "ValueError" in out["reason"]
    assert out.get("url", "") == ""


def test_to_url_still_maps_paths_to_file_uri(tmp_path):
    p = tmp_path / "page.html"
    p.write_text("hi")
    assert mcp_tabs.to_url(p) == p.resolve().as_uri()
    assert mcp_tabs.to_url(str(p)).startswith("file://")


# ── Finding E: no blind SIGKILL off /tmp/tahuti-pulse.pid ────────────────────
# round-4 review #3 moved the logic to scripts/avatar_pulse.py (shared with
# ptt_pynput's release path); these stay as the delegation checks.

def test_goal_runner_delegates_pulse_kill_to_avatar_pulse(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr("avatar_pulse.kill_avatar_pulse",
                        lambda pidfile="/tmp/tahuti-pulse.pid":
                        calls.append(pidfile) or True)
    assert runner._kill_avatar_pulse(pidfile=str(tmp_path / "x.pid")) is True
    assert calls == [str(tmp_path / "x.pid")]


def test_all_pulse_kill_sites_use_the_checked_helper():
    """Every site that retires the pulse must go through the checked helper —
    no bare os.kill(<pid>, 9) off the pid file may come back, in EITHER file."""
    for script in ("tahuti_goal_runner.py", "ptt_pynput.py"):
        src = (SCRIPTS / script).read_text(encoding="utf-8")
        lines = [ln for ln in src.splitlines() if "tahuti-pulse.pid" in ln]
        bare = [ln for ln in lines
                if ("os.kill" in ln or "read_text" in ln)
                and "_kill_avatar_pulse" not in ln
                and "kill_avatar_pulse" not in ln
                and "world-writable" not in ln]
        assert not bare, f"{script}: bare pid-file kill returned: {bare}"
    goal_src = (SCRIPTS / "tahuti_goal_runner.py").read_text(encoding="utf-8")
    assert goal_src.count("_kill_avatar_pulse()") == 3   # gate, slash card, job end
    assert "_kill_avatar_pulse(pidfile=" in goal_src     # _show_work_avatar's own file
    for script in ("tahuti_goal_runner.py", "ptt_pynput.py"):
        src = (SCRIPTS / script).read_text(encoding="utf-8")
        assert "import avatar_pulse" in src, f"{script} missing the shared import"


# ── Finding F: guide_card cannot be unbound in the finally anymore ───────────

def test_presenter_guide_card_initialized_and_guarded():
    tree = ast.parse(PRESENTER_SRC.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_main")
    seen_assign_none = False
    for node in ast.walk(fn):
        if (isinstance(node, ast.Assign)
                and getattr(node.targets[0], "id", "") == "guide_card"
                and getattr(node.value, "value", None) is None):
            seen_assign_none = True
        # ast.Finally does not exist: the finally body lives on ast.Try
        if isinstance(node, ast.Try) and node.finalbody:
            body_src = "\n".join(ast.unparse(n) for n in node.finalbody)
            if "guide_card.close()" in body_src:
                assert "if guide_card is not None" in body_src, (
                    "guide_card.close() in a finally without a None guard")
    assert seen_assign_none, "guide_card lost its None initialisation"


# ── Finding G (unmarked): a dangling flag is a clean JSON error ──────────────

def test_argv_value_dangling_flag_returns_none(monkeypatch):
    import tahuti_choice_presenter as presenter
    monkeypatch.setattr(presenter.sys, "argv", ["prog.py", "--send"])
    assert presenter._argv_value("--send") is None
    assert presenter._argv_value("--sendx") is None
    monkeypatch.setattr(presenter.sys, "argv", ["prog.py", "--send", "/hub"])
    assert presenter._argv_value("--send") == "/hub"


# ── Finding B (unmarked): the truncation never exceeds the limit ─────────────

def test_spoken_form_never_exceeds_limit():
    limit = speech_text.MAX_SPOKEN_CHARS
    one_token = speech_text.spoken_form("w" * 1234)
    assert len(one_token) <= limit and one_token.endswith("…")
    words = speech_text.spoken_form(" ".join(["word"] * 400))
    assert len(words) <= limit and words.endswith("…")


def test_spoken_form_one_long_token_hard_cuts():
    """OCR finding B: with no space at all there is no word boundary —
    hard-cut, and exactly one char is reserved for the ellipsis."""
    limit = speech_text.MAX_SPOKEN_CHARS
    out = speech_text.spoken_form("z" * (limit * 3))
    assert out == "z" * (limit - 1) + "…"
    assert len(out) == limit
