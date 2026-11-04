"""Tests for Tahuti goal runner file tools.

These tests never touch the live Meta API; the httpx client is replaced
with a fake. File-system reads are exercised through temporary paths with
the home-directory guard stubbed to pass.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import tahuti_goal_runner as runner  # noqa: E402


class _FakeClient:
    """Stub httpx.Client that returns queued Responses-API-shaped payloads."""

    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise RuntimeError("no more fake responses")

        class _FakeResponse:
            def __init__(self, data: dict):
                self._data = data

            def json(self) -> dict:
                return self._data

            def raise_for_status(self) -> None:
                pass

        return _FakeResponse(self.responses.pop(0))


def _text_response(text: str, response_id: str = "r1") -> dict:
    return {
        "id": response_id,
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
    }


def _function_call_response(
    calls: list[tuple[str, dict]], response_id: str = "r1"
) -> dict:
    output = []
    for i, (name, args) in enumerate(calls):
        output.append(
            {
                "type": "function_call",
                "call_id": f"call_{i}",
                "name": name,
                "arguments": json.dumps(args),
            }
        )
    return {"id": response_id, "output": output}


@pytest.fixture(autouse=True)
def _stub_meta_key(monkeypatch):
    monkeypatch.setattr(runner, "META_KEY", "fake-key-long-enough-for-the-check")


# ──────────────────────────────────────────────────────────────────────
# Read-only file tools
# ──────────────────────────────────────────────────────────────────────


def test_read_file_tool_reads_text(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_is_under_home", lambda p: True)
    note = tmp_path / "note.txt"
    note.write_text("hello world")
    result = runner._read_file_tool(str(note))
    assert result["ok"] is True
    assert result["content"] == "hello world"
    assert result["total_lines"] == 1


def test_read_file_tool_honours_offset_and_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_is_under_home", lambda p: True)
    note = tmp_path / "note.txt"
    note.write_text("line1\nline2\nline3\nline4")
    result = runner._read_file_tool(str(note), offset=2, limit=2)
    assert result["content"] == "line2\nline3"


def test_read_file_tool_rejects_outside_home(monkeypatch):
    monkeypatch.setattr(runner, "_is_under_home", lambda p: False)
    result = runner._read_file_tool("/etc/passwd")
    assert result["ok"] is False
    assert "outside home" in result["error"]


def test_read_file_tool_resolves_bare_filename_under_home(monkeypatch, tmp_path):
    """2026-11-01 contract change: a bare filename resolves under the
    operator's home instead of refusing — refusing forced the model to
    guess absolute paths and burn whole tool-call spirals guessing wrong."""
    note = tmp_path / "note.txt"
    note.write_text("resolved")

    def fake_home():
        return tmp_path

    monkeypatch.setattr(runner.Path, "home", staticmethod(fake_home))
    # Bug fixed 2026-11-01: this used to check against the REAL home
    # while Path.home() was patched to tmp_path -- the resolved path
    # (under tmp_path) could never satisfy a check anchored on the real
    # home, so the test failed regardless of whether the contract itself
    # worked. Must check against the same fake home _read_file_tool
    # actually resolves against.
    monkeypatch.setattr(runner, "_is_under_home", lambda p: tmp_path in p.parents or p == tmp_path)
    result = runner._read_file_tool("note.txt")
    assert result["ok"] is True
    assert result["content"] == "resolved"


def test_read_file_tool_still_refuses_home_escape(monkeypatch):
    monkeypatch.setattr(runner, "_is_under_home", lambda p: False)
    result = runner._read_file_tool("../../etc/passwd")
    assert result["ok"] is False
    assert "outside home" in result["error"]


def test_list_dir_tool_lists_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_is_under_home", lambda p: True)
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b").mkdir()
    result = runner._list_dir_tool(str(tmp_path))
    assert result["ok"] is True
    names = {e["name"] for e in result["entries"]}
    assert names == {"a.txt", "b"}


def test_list_dir_tool_rejects_outside_home(monkeypatch):
    monkeypatch.setattr(runner, "_is_under_home", lambda p: False)
    result = runner._list_dir_tool("/etc")
    assert result["ok"] is False
    assert "outside home" in result["error"]


# ──────────────────────────────────────────────────────────────────────
# Write proposal path
# ──────────────────────────────────────────────────────────────────────


def test_write_file_tool_queues_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_is_under_home", lambda p: True)
    monkeypatch.setattr(runner, "APPROVALS_DIR", tmp_path)
    # The approval box answers "once": the write executes and the queued
    # record remains as the audit trail.
    monkeypatch.setattr(runner, "_present_approval", lambda *a, **k: "once")
    target = tmp_path / "out.txt"
    result = runner._write_file_tool("g1", str(target), "new content", "create file")
    assert result["ok"] is True
    assert result["status"] == "executed"
    assert result["decision"] == "once"
    assert target.read_text(encoding="utf-8") == "new content"
    pending = list(tmp_path.glob("*.pending.json"))
    assert len(pending) == 1
    data = json.loads(pending[0].read_text())
    assert data["tool"] == "write_file"
    assert data["path"] == str(target)
    assert data["content"] == "new content"
    assert data["reason"] == "create file"
    assert data["status"] == "pending"


def test_write_file_tool_rejects_outside_home(monkeypatch):
    monkeypatch.setattr(runner, "_is_under_home", lambda p: False)
    result = runner._write_file_tool("g1", "/etc/passwd", "x")
    assert result["ok"] is False
    assert "outside home" in result["error"]


# ──────────────────────────────────────────────────────────────────────
# Tool execution dispatcher
# ──────────────────────────────────────────────────────────────────────


def test_execute_file_tool_runs_read_file(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_is_under_home", lambda p: True)
    note = tmp_path / "note.txt"
    note.write_text("dispatch ok")
    result = runner._execute_file_tool(
        "g1", {"name": "read_file", "arguments": json.dumps({"path": str(note)})},
        {"read_file"},
    )
    assert result["ok"] is True
    assert result["content"] == "dispatch ok"


def test_execute_file_tool_rejects_unknown_tool():
    result = runner._execute_file_tool(
        "g1", {"name": "delete_file", "arguments": "{}"}, {"delete_file"}
    )
    assert result["ok"] is False
    assert "unknown" in result["error"]


def test_execute_file_tool_handles_bad_arguments():
    result = runner._execute_file_tool(
        "g1", {"name": "read_file", "arguments": "not-json"}, {"read_file"}
    )
    assert result["ok"] is False
    assert "invalid JSON" in result["error"]


def test_process_goal_missing_id_archives_failed(tmp_path, monkeypatch):
    """Regression: a goal file without an 'id' field used to raise KeyError."""
    monkeypatch.setattr(runner, "GOALS_DIR", tmp_path / "goals")
    monkeypatch.setattr(runner, "DONE_DIR", tmp_path / "goals" / "done")
    monkeypatch.setattr(runner, "FAILED_DIR", tmp_path / "goals" / "failed")
    monkeypatch.setattr(runner, "_post_speak", lambda text: None)
    monkeypatch.setattr(runner, "_write_last_response", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "_notify", lambda *args, **kwargs: None)

    goal_path = runner.GOALS_DIR / "no_id.json"
    goal_path.parent.mkdir(parents=True, exist_ok=True)
    goal_path.write_text(
        json.dumps(
            {
                "transcript": "write a note",
                "type": "write",
                "time": "2026-10-29T18:00:00-0400",
                "source": "voice_bridge",
            }
        ),
        encoding="utf-8",
    )

    runner._process_goal(goal_path)

    failed = sorted(runner.FAILED_DIR.glob("*.json"))
    assert len(failed) == 1
    record = json.loads(failed[0].read_text())
    assert "missing required" in record["error"].lower()
    assert "id" in record["error"]


# ──────────────────────────────────────────────────────────────────────
# Model loop with file tools
# ──────────────────────────────────────────────────────────────────────


def test_call_model_returns_text_without_tool_calls(monkeypatch):
    fake = _FakeClient([_text_response("hello there", "r1")])
    monkeypatch.setattr(runner.httpx, "Client", lambda **kwargs: fake)
    text, model, elapsed, elapsed_s, route = runner._call_model("say hi")
    assert text == "hello there"
    assert len(fake.calls) == 1


def test_call_model_executes_read_file_tool_and_follows_up(monkeypatch):
    fake = _FakeClient(
        [
            _function_call_response(
                [("read_file", {"path": "/home/elijah/note.txt"})], "r1"
            ),
            _text_response("I read the note.", "r2"),
        ]
    )
    monkeypatch.setattr(runner.httpx, "Client", lambda **kwargs: fake)
    monkeypatch.setattr(runner, "_is_under_home", lambda p: True)
    monkeypatch.setattr(
        runner,
        "_read_file_tool",
        lambda path, offset=1, limit=None: {
            "ok": True,
            "content": "note content",
        },
    )
    text, model, elapsed, elapsed_s, route = runner._call_model("read my note", goal_id="g1")
    assert text == "I read the note."
    assert len(fake.calls) == 2
    second_payload = fake.calls[1][1]["json"]
    assert second_payload["previous_response_id"] == "r1"
    assert second_payload["input"][0]["type"] == "function_call_output"
    assert second_payload["input"][0]["call_id"] == "call_0"
    output = json.loads(second_payload["input"][0]["output"])
    assert output["ok"] is True
    assert output["content"] == "note content"


def test_call_model_queues_write_file_tool_and_follows_up(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "APPROVALS_DIR", tmp_path)
    monkeypatch.setattr(runner, "_present_approval", lambda *a, **k: "once")
    fake = _FakeClient(
        [
            _function_call_response(
                [
                    (
                        "write_file",
                        {
                            "path": "/home/elijah/notes.txt",
                            "content": "proposed",
                            "reason": "add note",
                        },
                    )
                ],
                "r1",
            ),
            _text_response("Queued for approval.", "r2"),
        ]
    )
    monkeypatch.setattr(runner.httpx, "Client", lambda **kwargs: fake)
    monkeypatch.setattr(runner, "_is_under_home", lambda p: True)
    text, model, elapsed, elapsed_s, route = runner._call_model("write a note", goal_id="g2")
    assert text == "Queued for approval."
    pending = list(tmp_path.glob("*.pending.json"))
    assert len(pending) == 1
    data = json.loads(pending[0].read_text())
    assert data["path"] == "/home/elijah/notes.txt"
    assert data["content"] == "proposed"


# ──────────────────────────────────────────────────────────────────────
# Goal-level approval must reach the avatar's one box (2026-10-31 bug):
# a gated question queued a .pending.json file and spoke "queued for
# you", but nothing ever presented the box, so no decision could happen.
# ──────────────────────────────────────────────────────────────────────


def _approval_goal(tmp_path, monkeypatch, transcript, outcome, calls):
    """A gated goal with every side effect stubbed; outcome is the box's."""
    monkeypatch.setattr(runner, "GOALS_DIR", tmp_path / "goals")
    monkeypatch.setattr(runner, "DONE_DIR", tmp_path / "goals" / "done")
    monkeypatch.setattr(runner, "FAILED_DIR", tmp_path / "goals" / "failed")
    monkeypatch.setattr(runner, "APPROVALS_DIR", tmp_path / "approvals")
    monkeypatch.setattr(runner, "GRANTS_FILE", tmp_path / "grants.json")
    monkeypatch.setattr(runner, "_post_speak", lambda text: None)
    monkeypatch.setattr(runner, "_write_last_response", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_notify", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_write_caption", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_show_work_avatar", lambda: None)

    def fake_model(text, goal_id=None, effort=None):
        calls.append(text)
        return "done it", "fake-model", "1s"

    monkeypatch.setattr(runner, "_call_model", fake_model)
    monkeypatch.setattr(runner, "_present_approval", lambda *a, **k: outcome)

    goal_path = runner.GOALS_DIR / "gated1.json"
    goal_path.parent.mkdir(parents=True, exist_ok=True)
    goal_path.write_text(
        json.dumps(
            {
                "id": "gated1",
                "transcript": transcript,
                "type": "write",
                "time": "2026-10-31T09:00:00-0400",
                "source": "voice_bridge",
            }
        ),
        encoding="utf-8",
    )
    return goal_path


def test_approval_box_yes_runs_the_goal(tmp_path, monkeypatch):
    calls = []
    goal_path = _approval_goal(
        tmp_path, monkeypatch, "run the backup please", "once", calls)
    runner._process_goal(goal_path)
    assert calls == ["run the backup please"]


def test_approval_box_deny_never_runs_the_goal(tmp_path, monkeypatch):
    calls = []
    goal_path = _approval_goal(
        tmp_path, monkeypatch, "run the backup please", "deny", calls)
    runner._process_goal(goal_path)
    assert calls == []


def test_approval_without_a_box_queues_as_before(tmp_path, monkeypatch):
    calls = []
    goal_path = _approval_goal(
        tmp_path, monkeypatch, "run the backup please", None, calls)
    runner._process_goal(goal_path)
    assert calls == []
    pending = list(runner.APPROVALS_DIR.glob("*.pending.json"))
    assert len(pending) == 1


def test_present_approval_maps_avatar_verdicts(tmp_path, monkeypatch):
    """The presenter answers once/session/always/deny after READY.

    Updated 2026-11-02 for the one-box presenter protocol: READY first
    (the card really is up), then the verdict line. A garbage verdict
    after READY fails closed as deny; a garbage first line means no
    card was shown at all, which is unavailable (None).
    """
    monkeypatch.setenv("DISPLAY", ":0")
    stub = tmp_path / "presenter.py"
    for spoken, expected in (("once", "once"), ("always", "session"),
                             ("session", "session"), ("deny", "deny"),
                             ("garbage", "deny")):
        stub.write_text(
            "import sys; sys.stdin.read(); print('READY', flush=True); "
            f"print({spoken!r}, flush=True)\n", encoding="utf-8")
        monkeypatch.setattr(runner, "_PRESENTER", stub)
        assert runner._present_approval("tahuti:run:background", "run it") == expected
    stub.write_text("print('garbage')\n", encoding="utf-8")
    monkeypatch.setattr(runner, "_PRESENTER", stub)
    assert runner._present_approval("tahuti:run:background", "run it") is None


def test_present_approval_headless_has_no_card(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    assert runner._present_approval("tahuti:run:background", "run it") is None


def test_approval_box_always_saves_a_grant(tmp_path, monkeypatch):
    calls = []
    goal_path = _approval_goal(
        tmp_path, monkeypatch, "run the backup please", "session", calls)
    runner._process_goal(goal_path)
    assert calls == ["run the backup please"]
    grants = json.loads(runner.GRANTS_FILE.read_text(encoding="utf-8"))
    assert grants["patterns"]["tahuti:run:background"] == "always"


def test_all_tools_includes_web_search_for_questions():
    tools = runner._all_tools("what is the weather?")
    assert any(t.get("type") == "web_search" for t in tools)
    assert any(t.get("name") == "read_file" for t in tools)


def test_all_tools_includes_file_tools_for_plain_requests():
    tools = runner._all_tools("summarize my notes")
    assert any(t.get("name") == "read_file" for t in tools)
    assert any(t.get("name") == "list_dir" for t in tools)
    assert any(t.get("name") == "write_file" for t in tools)
    assert not any(t.get("type") == "web_search" for t in tools)


# ──────────────────────────────────────────────────────────────────────
# request_approval: the model's seam to the operator's real card
# (2026-11-01) — before this tool existed, anything gated that was
# phrased without an APPROVAL_VERB dead-ended in a text refusal and
# no box ever appeared.
# ──────────────────────────────────────────────────────────────────────


def test_request_approval_once_returns_ok(monkeypatch):
    monkeypatch.setattr(runner, "_present_approval", lambda *a, **k: "once")
    result = runner._request_approval_tool(
        "g1", "restart the voice service", "it is a gated service action")
    assert result["ok"] is True
    assert result["decision"] == "once"


def test_request_approval_deny_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "GRANTS_FILE", tmp_path / "grants.json")
    monkeypatch.setattr(runner, "_present_approval", lambda *a, **k: "deny")
    result = runner._request_approval_tool(
        "g1", "delete notes.txt", "irreversible delete")
    assert result["ok"] is False
    assert result["decision"] == "deny"


def test_request_approval_headless_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "GRANTS_FILE", tmp_path / "grants.json")
    monkeypatch.setattr(runner, "_present_approval", lambda *a, **k: None)
    result = runner._request_approval_tool(
        "g1", "install vlc", "needs install rights")
    assert result["ok"] is False
    assert result["decision"] == "unavailable"


def test_request_approval_session_never_persists_grant(monkeypatch, tmp_path):
    """A session verdict must not silently become a permanent grant: the
    card's session and always buttons currently map to the same internal
    value, so persisting here would turn 'session' into 'always'."""
    monkeypatch.setattr(runner, "GRANTS_FILE", tmp_path / "grants.json")
    monkeypatch.setattr(runner, "_present_approval", lambda *a, **k: "session")
    result = runner._request_approval_tool(
        "g1", "restart voice service", "gated service action")
    assert result["ok"] is True
    assert result["decision"] == "session"
    assert not (tmp_path / "grants.json").exists()


def test_request_approval_skips_card_when_grant_covers(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "GRANTS_FILE", tmp_path / "grants.json")
    (tmp_path / "grants.json").write_text(json.dumps(
        {"patterns": {"tahuti:install:background": "always"}}))

    def boom(*a, **k):
        raise AssertionError("the card must not be raised for a covered grant")

    monkeypatch.setattr(runner, "_present_approval", boom)
    result = runner._request_approval_tool(
        "g1", "install vlc", "needs install rights")
    assert result["ok"] is True
    assert result["decision"] == "already-granted"


def test_request_approval_requires_action():
    result = runner._request_approval_tool("g1", "", "why")
    assert result["ok"] is False
    assert "action required" in result["error"]


def test_request_approval_offered_on_every_route():
    names = {t.get("name") for t in runner._all_tools("summarize my notes")}
    assert "request_approval" in names
    # fast lanes append it inside _call_model; assert the tool exists and
    # the prompt points the model at it.
    assert runner.REQUEST_APPROVAL_TOOL["name"] == "request_approval"
    assert "request_approval" in runner.SAFETY_PROMPT_TAIL


def test_request_approval_dispatched_through_execute_tool(monkeypatch):
    monkeypatch.setattr(
        runner, "_request_approval_tool",
        lambda gid, action, reason: {"ok": True, "decision": "once"})
    result = runner._execute_file_tool(
        "g9",
        {"name": "request_approval",
         "arguments": json.dumps({"action": "restart service",
                                  "reason": "gated"})},
        {"request_approval"},
    )
    assert result["ok"] is True
    assert result["decision"] == "once"


def test_request_approval_refused_when_not_declared(monkeypatch):
    monkeypatch.setattr(
        runner, "_request_approval_tool", lambda *a, **k: {"ok": True})
    result = runner._execute_file_tool(
        "g9", {"name": "request_approval", "arguments": "{}"}, set())
    assert result["ok"] is False
    assert "not available" in result["error"]


def test_fast_chat_lane_offers_request_approval(monkeypatch):
    fake = _FakeClient([_text_response("hi there", "r1")])
    monkeypatch.setattr(runner.httpx, "Client", lambda **kwargs: fake)
    runner._call_model("say hi")
    payload = fake.calls[0][1]["json"]
    names = {t.get("name") for t in payload["tools"]}
    assert "request_approval" in names


def test_approved_request_approval_escalates_to_full_tools(
        monkeypatch, tmp_path):
    """The operator's 'yes' must re-arm the model with the real toolset:
    permission without capability was approval theater (observed live —
    'approved but I don't have a file-listing tool this turn')."""
    monkeypatch.setattr(runner, "GRANTS_FILE", tmp_path / "grants.json")
    monkeypatch.setattr(runner, "_present_approval", lambda *a, **k: "once")
    monkeypatch.setattr(
        runner, "_list_dir_tool",
        lambda path, show_hidden=False: {
            "ok": True,
            "entries": [{"name": "mcp_skill", "type": "dir"}],
        })
    fake = _FakeClient(
        [
            _function_call_response(
                [("request_approval",
                  {"action": "search my skills", "reason": "needs file access"})],
                "r1"),
            _function_call_response(
                [("list_dir", {"path": "/home/elijah/skills"})], "r2"),
            _text_response("I searched skills and found mcp_skill.", "r3"),
        ]
    )
    monkeypatch.setattr(runner.httpx, "Client", lambda **kwargs: fake)

    text, _m, _e, _s, route = runner._call_model("hi", goal_id="g1")

    assert route == "fast-chat"
    assert text == "I searched skills and found mcp_skill."
    second = fake.calls[1][1]["json"]
    names = {t.get("name") for t in second["tools"]}
    assert "read_file" in names and "list_dir" in names
    # the show-your-work nudge rides along with the approval result
    nudges = [o for o in second["input"] if o.get("role") == "user"]
    assert nudges and "APPROVED" in nudges[-1]["content"]
    # the escalated list_dir call actually executed (round budget reset)
    third = fake.calls[2][1]["json"]
    out = json.loads(third["input"][0]["output"])
    assert out["ok"] is True
    assert out["entries"][0]["name"] == "mcp_skill"


def test_denied_request_approval_does_not_escalate(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "GRANTS_FILE", tmp_path / "grants.json")
    monkeypatch.setattr(runner, "_present_approval", lambda *a, **k: "deny")
    fake = _FakeClient(
        [
            _function_call_response(
                [("request_approval",
                  {"action": "delete notes.txt", "reason": "irreversible"})],
                "r1"),
            _text_response("The operator denied it.", "r2"),
        ]
    )
    monkeypatch.setattr(runner.httpx, "Client", lambda **kwargs: fake)

    text, _m, _e, _s, route = runner._call_model("hi", goal_id="g2")

    assert text == "The operator denied it."
    second = fake.calls[1][1]["json"]
    names = {t.get("name") for t in second["tools"]}
    # deny must NOT hand over the file tools the thin lane never had
    assert "read_file" not in names
    assert "list_dir" not in names
    # and no approval nudge was injected
    nudges = [o for o in second["input"] if o.get("role") == "user"]
    assert not nudges


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))


# ──────────────────────────────────────────────────────────────────────
# Escalation layer (2026-11-02): one-box presenter, gated opens,
# bounded escalation, deterministic clock, honest failure labels.
# Motivated by the 2026-11-02 end-to-end battery: open_url executed
# with no approval, the ninja presenter timed out at 150s a call, a
# write goal burned 1127s on stacked waits, and fast-lookup invented
# the time of day.
# ──────────────────────────────────────────────────────────────────────

import os  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402

# ── open_url / open_app are gated like every desktop action ──────────

def test_open_url_unavailable_when_no_presenter(monkeypatch):
    launches = []
    monkeypatch.setattr(runner, "_present_approval", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_is_granted", lambda pattern: False)
    monkeypatch.setattr(runner.subprocess, "Popen",
                        lambda *a, **k: launches.append(a))
    result = runner._open_url_tool("g1", "facebook.com")
    assert result["ok"] is False
    assert result["status"] == "unavailable"
    assert "approval card could not be shown" in result["error"]
    assert launches == []  # nothing launched — the battery's Chrome proof


def test_open_url_executes_after_once_approval(monkeypatch):
    """Agent-opened tabs are MCP tabs (operator rule 2026-11-04) -- and a test must
    never open a real one, so mcp_tabs.open_tab is replaced."""
    import mcp_tabs
    opened = []
    monkeypatch.setattr(runner, "_present_approval", lambda *a, **k: "once")
    monkeypatch.setattr(runner, "_is_granted", lambda pattern: False)
    monkeypatch.setattr(mcp_tabs, "open_tab", lambda u: opened.append(u) or {"ok": True})
    monkeypatch.setattr(runner.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("must not open a bare xdg-open tab"))
    result = runner._open_url_tool("g1", "facebook.com")
    assert result["ok"] is True
    assert result["opened"] == "https://facebook.com"
    assert result["via"] == "mcp-tab"
    assert opened == ["https://facebook.com"]


def test_open_url_bridge_down_fails_without_a_bare_tab(monkeypatch):
    import mcp_tabs
    monkeypatch.setattr(runner, "_present_approval", lambda *a, **k: "once")
    monkeypatch.setattr(runner, "_is_granted", lambda pattern: False)
    monkeypatch.setattr(mcp_tabs, "open_tab",
                        lambda u: {"ok": False, "reason": "bridge_unreachable"})
    monkeypatch.setattr(runner.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("must not fall back to xdg-open"))
    result = runner._open_url_tool("g1", "example.com")
    assert result["ok"] is False
    assert "MCP tab" in result["error"]


def test_open_url_session_approval_saves_grant(monkeypatch):
    import mcp_tabs
    saved = []
    monkeypatch.setattr(runner, "_present_approval", lambda *a, **k: "session")
    monkeypatch.setattr(runner, "_is_granted", lambda pattern: False)
    monkeypatch.setattr(runner, "_save_grant", saved.append)
    monkeypatch.setattr(mcp_tabs, "open_tab", lambda u: {"ok": True})
    result = runner._open_url_tool("g1", "example.com")
    assert result["ok"] is True
    assert saved == ["tahuti:open_url:background"]


def test_open_url_denied_by_operator(monkeypatch):
    monkeypatch.setattr(runner, "_present_approval", lambda *a, **k: "deny")
    monkeypatch.setattr(runner, "_is_granted", lambda pattern: False)
    monkeypatch.setattr(runner.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("must not launch"))
    result = runner._open_url_tool("g1", "facebook.com")
    assert result["ok"] is False
    assert result["status"] == "denied"


def test_open_url_saved_grant_skips_box(monkeypatch):
    import mcp_tabs
    monkeypatch.setattr(runner, "_is_granted", lambda pattern: True)
    monkeypatch.setattr(runner, "_present_approval",
                        lambda *a, **k: pytest.fail("box must not be shown"))
    monkeypatch.setattr(mcp_tabs, "open_tab", lambda u: {"ok": True})
    result = runner._open_url_tool("g1", "example.com")
    assert result["ok"] is True


def test_open_app_unavailable_when_no_presenter(monkeypatch):
    monkeypatch.setattr(runner, "_present_approval", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_is_granted", lambda pattern: False)
    monkeypatch.setattr(runner, "_launch_visible",
                        lambda *a, **k: pytest.fail("must not launch"))
    result = runner._open_app_tool("g1", "chrome")
    assert result["ok"] is False
    assert result["status"] == "unavailable"


# ── presenter transport: fail fast, honest readiness ─────────────────

def _write_stub(tmp_path, body: str):
    stub = tmp_path / "stub_presenter.py"
    stub.write_text(body, encoding="utf-8")
    return stub


def test_run_presenter_no_display_returns_none(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    assert runner._run_presenter("p", "d", {}) is None


def test_run_presenter_unavailable_stub_fails_fast(tmp_path, monkeypatch):
    stub = _write_stub(
        tmp_path, "import sys; print('UNAVAILABLE no display'); sys.exit(2)\n")
    monkeypatch.setattr(runner, "_PRESENTER", stub)
    monkeypatch.setenv("DISPLAY", ":0")
    t0 = time.monotonic()
    assert runner._run_presenter("p", "d", {}) is None
    assert time.monotonic() - t0 < 5


def test_run_presenter_silent_stub_fails_fast(tmp_path, monkeypatch):
    stub = _write_stub(tmp_path, "import time; time.sleep(60)\n")
    monkeypatch.setattr(runner, "_PRESENTER", stub)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(runner, "_PRESENT_READY_TIMEOUT", 1.0)
    t0 = time.monotonic()
    assert runner._run_presenter("p", "d", {}) is None
    assert time.monotonic() - t0 < 10


def test_run_presenter_ready_then_verdict(tmp_path, monkeypatch):
    stub = _write_stub(
        tmp_path,
        "import sys; sys.stdin.read(); print('READY', flush=True); "
        "print('once', flush=True)\n")
    monkeypatch.setattr(runner, "_PRESENTER", stub)
    monkeypatch.setenv("DISPLAY", ":0")
    assert runner._run_presenter("p", "d", {}) == "once"


def test_presenter_script_unavailable_without_display():
    presenter = SCRIPTS / "tahuti_approval_presenter.py"
    env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
    run = subprocess.run(
        [sys.executable, str(presenter)],
        input=json.dumps({"pattern_key": "p", "description": "d"}),
        capture_output=True, text=True, env=env, timeout=30)
    assert run.returncode == 2
    assert run.stdout.startswith("UNAVAILABLE")


# ── per-goal escalation bound: never present twice, never retry ──────

def test_escalation_unavailable_not_retried_within_goal(monkeypatch):
    calls = []

    def fake_run(pattern, description, extra):
        calls.append(pattern)
        return None

    monkeypatch.setattr(runner, "_run_presenter", fake_run)
    gid = "goal-cache-unavailable"
    try:
        assert runner._present_approval("p:a", "x", goal_id=gid) is None
        # A different pattern in the same goal must NOT present again:
        # the presenter is unavailable, full stop.
        assert runner._present_approval("p:b", "y", goal_id=gid) is None
        assert calls == ["p:a"]
    finally:
        runner._escalation_forget(gid)
    # After the goal ends, a fresh goal may try again.
    assert runner._present_approval("p:b", "y", goal_id=gid) is None
    assert calls == ["p:a", "p:b"]
    runner._escalation_forget(gid)


def test_escalation_verdict_cached_per_pattern(monkeypatch):
    calls = []

    def fake_run(pattern, description, extra):
        calls.append(pattern)
        return "deny"

    monkeypatch.setattr(runner, "_run_presenter", fake_run)
    gid = "goal-cache-verdict"
    try:
        assert runner._present_approval("p:a", "x", goal_id=gid) == "deny"
        assert runner._present_approval("p:a", "x", goal_id=gid) == "deny"
        assert runner._present_approval("p:b", "y", goal_id=gid) == "deny"
        assert calls == ["p:a", "p:b"]  # p:a presented once, p:b once
    finally:
        runner._escalation_forget(gid)


# ── write_file: unavailable fails fast and says so ───────────────────

def test_write_file_tool_unavailable_fails_fast(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_is_under_home", lambda p: True)
    monkeypatch.setattr(runner, "APPROVALS_DIR", tmp_path)
    monkeypatch.setattr(runner, "_present_approval", lambda *a, **k: None)
    target = tmp_path / "note.txt"
    t0 = time.monotonic()
    result = runner._write_file_tool("g-unavail", str(target), "content", "test")
    # Before the fix this path stacked 150s of subprocess timeout plus
    # 150s of receipt polling per call.
    assert time.monotonic() - t0 < 5
    assert result["ok"] is False
    assert result["status"] == "unavailable"
    assert "approval card could not be shown" in result["error"]
    assert not target.exists()
    # The request is closed out, not left as a live-looking pending.
    assert not list(tmp_path.glob("*.pending.json"))
    resolved = list(tmp_path.glob("*.resolved.json"))
    assert resolved
    assert json.loads(resolved[0].read_text())["status"] == "unavailable"


# ── deterministic clock ──────────────────────────────────────────────

def test_is_time_question_matches_direct_forms_only():
    assert runner._is_time_question("What time is it in Indianapolis right now?")
    assert runner._is_time_question("What's the date today?")
    assert runner._is_time_question("What day is it?")
    assert runner._is_time_question("Tell me the time")
    assert not runner._is_time_question("What time does the store open?")
    assert not runner._is_time_question("How much time does it take to bake bread?")
    assert not runner._is_time_question("Check my email")


def test_process_time_reports_real_indianapolis_time():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    text = runner._process_time()
    now = datetime.now(ZoneInfo("America/Indiana/Indianapolis"))
    assert now.strftime("%A") in text       # correct weekday
    assert now.strftime("%B") in text       # correct month
    assert str(now.day) in text             # correct day of month
    assert str(now.year) in text
    assert now.strftime("%Z") in text       # EST or EDT, as in force
    assert "Indianapolis" in text


# ── goal level: honest gate label, and the queue moves on ────────────

def test_process_goal_gate_unavailable_honest_then_next_goal_runs(tmp_path, monkeypatch):
    goals = tmp_path / "goals"
    done = goals / "done"
    failed = goals / "failed"
    for d in (goals, done, failed):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(runner, "GOALS_DIR", goals)
    monkeypatch.setattr(runner, "DONE_DIR", done)
    monkeypatch.setattr(runner, "FAILED_DIR", failed)
    monkeypatch.setattr(runner, "APPROVALS_DIR", tmp_path / "approvals")
    monkeypatch.setattr(runner, "_is_granted", lambda pattern: False)
    monkeypatch.setattr(runner, "_present_approval", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_post_speak", lambda text: None)
    monkeypatch.setattr(runner, "_write_last_response", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_notify", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_write_caption", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_show_work_avatar", lambda: None)

    g1 = {"id": "gate-goal-1", "transcript": "Delete the scratch file",
          "type": "task", "time": "t", "source": "test"}
    p1 = goals / "gate-goal-1.json"
    p1.write_text(json.dumps(g1), encoding="utf-8")
    runner._process_goal(p1)
    rec = json.loads((done / "gate-goal-1.json").read_text(encoding="utf-8"))
    assert "approval card" in rec["response"]
    assert "nothing was done" in rec["response"]
    assert "queued" not in rec["response"].lower()

    # The very next goal is processed normally — no stall, no residue.
    g2 = {"id": "time-goal-1", "transcript": "What time is it right now?",
          "type": "task", "time": "t", "source": "test"}
    p2 = goals / "time-goal-1.json"
    p2.write_text(json.dumps(g2), encoding="utf-8")
    runner._process_goal(p2)
    rec2 = json.loads((done / "time-goal-1.json").read_text(encoding="utf-8"))
    assert rec2["model"] == "deterministic-time"
    assert "Indianapolis" in rec2["response"]
