"""Tests for Tahuti goal runner file tools.

These tests never touch the live Meta API; the httpx client is replaced
with a fake. File-system reads are exercised through temporary paths with
the home-directory guard stubbed to pass.
"""

from __future__ import annotations

import json
import multiprocessing
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import tahuti_goal_runner as runner  # noqa: E402
import voice_goal_schema as _SCHEMA  # noqa: E402

# Runner validation uses the schema module from this canonical checkout.
runner.vgs = _SCHEMA

class _FakeClient:
    """Stub httpx.Client that returns queued Responses-API-shaped payloads."""

    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url: str, **kwargs) -> "_FakeResponse":
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
def _stub_meta_key(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "META_KEY", "fake-key-long-enough-for-the-check")
    # Never let any test observe or mutate the operator's persistent grants.
    monkeypatch.setattr(runner, "GRANTS_FILE", tmp_path / "grants.json")


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


def test_write_file_refuses_before_approval_or_write_even_when_approved(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_is_under_home", lambda p: True)
    approvals = tmp_path / "approvals"
    monkeypatch.setattr(runner, "APPROVALS_DIR", approvals)
    monkeypatch.setattr(runner, "_present_approval", lambda *a, **k: "once")
    monkeypatch.setattr(runner, "_is_granted", lambda key: True)
    target = tmp_path / "out.txt"

    result = runner._write_file_tool("g1", str(target), "new content", "create file")

    assert result["ok"] is False
    assert result["status"] == "refused"
    assert "file writes are disabled" in result["error"]
    assert not target.exists()
    assert not approvals.exists()


def test_write_file_refusal_is_not_bypassable_through_dispatcher(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_is_under_home", lambda p: True)
    approvals = tmp_path / "approvals"
    monkeypatch.setattr(runner, "APPROVALS_DIR", approvals)
    monkeypatch.setattr(runner, "_present_approval", lambda *a, **k: "always")
    monkeypatch.setattr(runner, "_is_granted", lambda key: True)
    monkeypatch.setattr(runner, "_write_to_agent_thread", lambda *a, **k: True)
    target = tmp_path / "dispatcher.txt"

    result = runner._execute_file_tool(
        "g1", {"name": "write_file", "arguments": json.dumps({
            "path": str(target), "content": "payload", "reason": "test"})},
        {"write_file"},
    )

    assert result["ok"] is False
    assert result["status"] == "refused"
    assert not target.exists()
    assert not approvals.exists()


def test_write_file_refuses_arbitrary_content_lengths(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_is_under_home", lambda p: True)
    approvals = tmp_path / "approvals"
    monkeypatch.setattr(runner, "APPROVALS_DIR", approvals)
    target = tmp_path / "long.txt"
    result = runner._write_file_tool(
        "g1", str(target), "x" * (runner.MAX_APPROVAL_PREVIEW_CHARS + 1), "large")
    assert result["ok"] is False
    assert result["status"] == "refused"
    assert not target.exists()
    assert not approvals.exists()


def test_write_file_tool_refuses_before_path_validation(monkeypatch):
    monkeypatch.setattr(runner, "_is_under_home", lambda p: False)
    result = runner._write_file_tool("g1", "/etc/passwd", "x")
    assert result["ok"] is False
    assert result["status"] == "refused"
    assert "file writes are disabled" in result["error"]


def test_env_cannot_enable_file_writes_or_expose_controls(tmp_path, monkeypatch):
    monkeypatch.setenv("TAHUTI_INFORMED_FILE_WRITE", "1")
    target = tmp_path / "never-created.txt"
    displayed = []
    monkeypatch.setattr(runner, "_present_approval", lambda *a, **k: displayed.append((a, k)) or "once")

    result = runner._write_file_tool("g", str(target), "secret payload", "test")
    tools = runner._all_tools("create a file")

    assert result["ok"] is False
    assert result["status"] == "refused"
    assert "file writes are disabled" in result["error"]
    assert not target.exists()
    assert displayed == []
    assert not any(t.get("name") == "write_file" for t in tools)
    assert "only when explicitly enabled" not in runner.SAFETY_PROMPT_TAIL


def test_write_file_refuses_without_showing_payload_or_writing(tmp_path, monkeypatch):
    target = tmp_path / "never-created.txt"
    displayed = []
    monkeypatch.setattr(runner, "_present_approval", lambda *a, **k: displayed.append((a, k)) or "once")

    result = runner._write_file_tool("g", str(target), "private payload", "reason")

    assert result["ok"] is False
    assert result["status"] == "refused"
    assert not target.exists()
    assert displayed == []


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
    note_path = Path("/home/elijah/note.txt")
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


def test_call_model_write_file_refuses_and_follows_up(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "APPROVALS_DIR", tmp_path / "approvals")
    fake = _FakeClient(
        [
            _function_call_response(
                [("write_file", {"path": "/home/elijah/notes.txt",
                                 "content": "proposed", "reason": "add note"})], "r1"),
            _text_response("File write refused: approval cannot show the full content.", "r2"),
        ]
    )
    monkeypatch.setattr(runner.httpx, "Client", lambda **kwargs: fake)
    monkeypatch.setattr(runner, "_is_under_home", lambda p: True)
    target = tmp_path / "notes.txt"
    fake = _FakeClient(
        [
            _function_call_response(
                [("write_file", {"path": str(target),
                                 "content": "proposed", "reason": "add note"})], "r1"),
            _text_response("File write refused: approval cannot show the full content.", "r2"),
        ]
    )
    monkeypatch.setattr(runner.httpx, "Client", lambda **kwargs: fake)
    text, model, elapsed, elapsed_s, route = runner._call_model("write a note", goal_id="g2")
    assert text.startswith("File write refused")
    assert not (tmp_path / "approvals").exists()
    assert not target.exists()


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


def test_approval_unavailable_never_runs_the_goal(tmp_path, monkeypatch):
    calls = []
    goal_path = _approval_goal(
        tmp_path, monkeypatch, "run the backup please", None, calls)
    runner._process_goal(goal_path)
    assert calls == []
    pending = list(runner.APPROVALS_DIR.glob("*.pending.json"))
    assert len(pending) == 1


def _approval_process_worker(description, result_queue):
    result_queue.put((description, runner._present_approval("tahuti:run:background", description)))


def test_present_approval_serializes_processes_and_keeps_verdicts_isolated(monkeypatch, tmp_path):
    """Separate requesters queue; a deny/error cannot leak across card IDs."""
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(runner, "_APPROVAL_CARD_LOCK", tmp_path / "card.lock")
    events = tmp_path / "events.log"

    class _Proc:
        returncode = 0

        def communicate(self, text):
            description = next(name for name in ("request-denies", "request-fails") if name in text)
            with events.open("a", encoding="utf-8") as stream:
                stream.write(f"start:{description}\n")
            time.sleep(0.2)
            with events.open("a", encoding="utf-8") as stream:
                stream.write(f"end:{description}\n")
            if description == "request-fails":
                self.returncode = 1
                return "deny\n", "synthetic failure"
            return "deny\n", ""

    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: _Proc())
    ctx = multiprocessing.get_context("fork")
    results = ctx.Queue()
    processes = [ctx.Process(target=_approval_process_worker, args=(name, results))
                 for name in ("request-denies", "request-fails")]
    for process in processes:
        process.start()
        time.sleep(0.05)
    received = [results.get(timeout=5) for _ in processes]
    for process in processes:
        process.join(timeout=5)
        assert process.exitcode == 0

    assert dict(received) == {"request-denies": "deny", "request-fails": None}
    lines = events.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    assert lines[0].startswith("start:") and lines[1] == "end:" + lines[0][6:]
    assert lines[2].startswith("start:") and lines[3] == "end:" + lines[2][6:]
    assert lines[0][6:] != lines[2][6:]


def test_present_approval_maps_avatar_verdicts(monkeypatch):
    """The avatar surface answers once/session/always/deny on stdout."""
    monkeypatch.setenv("DISPLAY", ":0")

    class _Proc:
        def __init__(self, out, returncode=0):
            self.stdout = out
            self.returncode = returncode
            self.input = None

        def communicate(self, text):
            self.input = text
            return self.stdout, ""

    for spoken, expected in (("once", "once"), ("always", "always"),
                             ("session", "session"), ("deny", "deny"),
                             ("garbage", None)):
        process = _Proc(spoken + "\n")
        monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: process)
        assert runner._present_approval("tahuti:run:background", "run it") == expected
        assert process.input


def test_present_approval_waits_without_timeout(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    calls = []

    class _Proc:
        returncode = 0
        def communicate(self, text):
            calls.append(text)
            return "once\n", ""

    def fake_popen(*args, **kwargs):
        assert "timeout" not in kwargs
        return _Proc()

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    assert runner._present_approval("tahuti:run:background", "run it") == "once"
    assert len(calls) == 1


def test_present_approval_process_failure_fails_closed(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")

    class _Proc:
        returncode = 1
        def communicate(self, text):
            return "once\n", "crashed"

    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: _Proc())
    assert runner._present_approval("tahuti:run:background", "run it") is None


def test_present_approval_spawn_failure_fails_closed(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(
        runner.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError("missing")))
    assert runner._present_approval("tahuti:run:background", "run it") is None


def test_present_approval_headless_has_no_card(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    assert runner._present_approval("tahuti:run:background", "run it") is None


def test_goal_session_never_persists_and_broad_grant_is_ignored(tmp_path, monkeypatch):
    calls = []
    goal_path = _approval_goal(
        tmp_path, monkeypatch, "run the backup please", "session", calls)
    runner.GRANTS_FILE.write_text(json.dumps(
        {"patterns": {"tahuti:run:background": "always"}}), encoding="utf-8")
    runner._process_goal(goal_path)
    assert calls == ["run the backup please"]
    grants = json.loads(runner.GRANTS_FILE.read_text(encoding="utf-8"))
    assert grants["patterns"]["tahuti:run:background"] == "always"


def test_goal_always_does_not_persist_verb_grant(tmp_path, monkeypatch):
    calls = []
    goal_path = _approval_goal(
        tmp_path, monkeypatch, "run the backup please", "always", calls)
    runner._process_goal(goal_path)
    assert calls == ["run the backup please"]
    assert not runner.GRANTS_FILE.exists()


def test_all_tools_includes_web_search_for_questions():
    tools = runner._all_tools("what is the weather?")
    assert any(t.get("type") == "web_search" for t in tools)
    assert any(t.get("name") == "read_file" for t in tools)


def test_all_tools_includes_file_tools_for_plain_requests():
    tools = runner._all_tools("summarize my notes")
    assert any(t.get("name") == "read_file" for t in tools)
    assert any(t.get("name") == "list_dir" for t in tools)
    assert not any(t.get("name") == "write_file" for t in tools)
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
    assert "exact requested action" in result["note"]
    assert "unrelated capabilities" in result["note"]
    assert "never persisted" in result["note"]
    assert not (tmp_path / "grants.json").exists()


def test_request_approval_shows_card_even_when_grant_covers(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "GRANTS_FILE", tmp_path / "grants.json")
    runner.GRANTS_FILE.write_text(json.dumps(
        {"patterns": {"tahuti:install:background": "always"}}))
    shown = []
    monkeypatch.setattr(
        runner, "_present_approval",
        lambda *a, **k: shown.append((a, k)) or "once")
    result = runner._request_approval_tool(
        "g1", "install vlc", "needs install rights")
    assert result["ok"] is True
    assert result["decision"] == "once"
    assert len(shown) == 1
    # A repeated approval request still presents a fresh card and does not
    # rewrite/clear the pre-existing grant state.
    result2 = runner._request_approval_tool(
        "g1", "install vlc", "needs install rights")
    assert result2["ok"] is True
    assert result2["decision"] == "once"
    assert len(shown) == 2
    assert json.loads(runner.GRANTS_FILE.read_text())["patterns"][
        "tahuti:install:background"] == "always"


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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
