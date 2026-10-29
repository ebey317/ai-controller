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


def test_read_file_tool_rejects_relative_path():
    result = runner._read_file_tool("note.txt")
    assert result["ok"] is False
    assert "absolute" in result["error"]


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
    target = tmp_path / "out.txt"
    result = runner._write_file_tool("g1", str(target), "new content", "create file")
    assert result["ok"] is True
    assert "queued" in result
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
        "g1", {"name": "read_file", "arguments": json.dumps({"path": str(note)})}
    )
    assert result["ok"] is True
    assert result["content"] == "dispatch ok"


def test_execute_file_tool_rejects_unknown_tool():
    result = runner._execute_file_tool(
        "g1", {"name": "delete_file", "arguments": "{}"}
    )
    assert result["ok"] is False
    assert "unknown" in result["error"]


def test_execute_file_tool_handles_bad_arguments():
    result = runner._execute_file_tool(
        "g1", {"name": "read_file", "arguments": "not-json"}
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
    text, model, elapsed = runner._call_model("say hi")
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
    text, model, elapsed = runner._call_model("read my note", goal_id="g1")
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
    text, model, elapsed = runner._call_model("write a note", goal_id="g2")
    assert text == "Queued for approval."
    pending = list(tmp_path.glob("*.pending.json"))
    assert len(pending) == 1
    data = json.loads(pending[0].read_text())
    assert data["path"] == "/home/elijah/notes.txt"
    assert data["content"] == "proposed"


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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
