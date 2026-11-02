"""Isolated tests for the voice bridge HTTP routing."""
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Optional

import pytest
from fastapi.testclient import TestClient

# Use the project venv packages and make voice_bridge importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

# Import under a private name so this test module cannot replace or retain a
# caller's `voice_bridge` module. Restore environment immediately after import.
_bridge_path = Path(__file__).resolve().parent.parent / "scripts" / "voice_bridge.py"
_bridge_spec = importlib.util.spec_from_file_location("_voice_bridge_tests", _bridge_path)
_bridge = importlib.util.module_from_spec(_bridge_spec)
_old_groq = os.environ.get("GROQ_API_KEY")
_old_bridge_key = os.environ.get("VOICE_BRIDGE_API_KEY")
os.environ["GROQ_API_KEY"] = "test-groq-key"
os.environ["VOICE_BRIDGE_API_KEY"] = ""
try:
    _bridge_spec.loader.exec_module(_bridge)
finally:
    if _old_groq is None:
        os.environ.pop("GROQ_API_KEY", None)
    else:
        os.environ["GROQ_API_KEY"] = _old_groq
    if _old_bridge_key is None:
        os.environ.pop("VOICE_BRIDGE_API_KEY", None)
    else:
        os.environ["VOICE_BRIDGE_API_KEY"] = _old_bridge_key
vb = _bridge


class _FakeResponse:
    def __init__(self, status_code: int = 200, json_data: Optional[dict] = None):
        self.status_code = status_code
        self._json = json_data or {}

    def json(self) -> dict:
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=self
            )


@pytest.fixture

def client(monkeypatch):
    """Prevent every test request from reaching live services or goal queues."""
    monkeypatch.setattr(vb, "_speak_bg", lambda text: None)
    # Tests exercise HTTP/STT/LLM routing, not local hardware commands.
    monkeypatch.setattr(vb.tts_control, "handle_driver_command", lambda text: None)
    return TestClient(vb.app)


def _mock_response(monkeypatch, response: _FakeResponse) -> None:
    """Replace the bridge's outbound HTTP client for this test only."""
    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return response

    monkeypatch.setattr(vb.httpx, "AsyncClient", _FakeClient)


@pytest.fixture

def live_bridge(monkeypatch, tmp_path):
    """Import the actual live-tree bridge with all writes redirected to tmp_path."""
    live_tree = Path(os.environ.get("TAHUTI_LIVE_TREE", Path.home() / "ai-controller")).expanduser()
    live_bridge_path = live_tree / "scripts" / "voice_bridge.py"
    if not live_bridge_path.is_file():
        pytest.skip(f"live voice bridge not found: {live_bridge_path}")
    spec = importlib.util.spec_from_file_location(
        "voice_bridge_live_regression", live_bridge_path
    )
    module = importlib.util.module_from_spec(spec)
    # Keep startup side effects (logs/config) inside the test sandbox too.
    import ai_controller_paths

    monkeypatch.setattr(ai_controller_paths, "ai_controller_dir", lambda: str(tmp_path))
    monkeypatch.setattr(ai_controller_paths, "config_dir", lambda: str(tmp_path))
    monkeypatch.setattr(ai_controller_paths, "load_env", lambda: {})
    monkeypatch.setenv("GROQ_API_KEY", "isolated-test-key")
    spec.loader.exec_module(module)
    isolated_goals = tmp_path / "goals"
    module.GOALS_DIR = isolated_goals
    monkeypatch.setattr(module, "_speak_bg", lambda text: None)
    monkeypatch.setattr(module.tts_control, "handle_driver_command", lambda text: None)
    return module, TestClient(module.app), isolated_goals


def test_live_execute_routes_goal_only_into_isolated_directory(live_bridge, monkeypatch):
    module, client, isolated_goals = live_bridge
    response = client.post(
        "/voice", data={"text": "Create a regression test", "mode": "execute"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["routed"] == "task"
    assert body["goal_type"] == "create"
    goal_path = Path(body["goal_path"])
    assert goal_path.parent == isolated_goals
    payload = json.loads(goal_path.read_text())
    assert payload["transcript"] == "Create a regression test"
    assert payload["type"] == "create"
    assert payload["source"] == "voice_bridge"
    assert list(isolated_goals.glob("*.json")) == [goal_path]


def test_live_execute_goal_write_failure_returns_server_error_without_live_queue(
    live_bridge, monkeypatch
):
    module, client, isolated_goals = live_bridge
    def fail_write(transcript):
        raise OSError("isolated goal storage unavailable")

    monkeypatch.setattr(module, "_write_goal_file", fail_write)
    with pytest.raises(OSError, match="isolated goal storage unavailable"):
        client.post("/voice", data={"text": "Create a regression test", "mode": "execute"})
    assert not isolated_goals.exists()
    assert not list(Path.home().joinpath(".tahuti", "goals").glob("*.json"))


def test_transcribe_only_with_text(client):
    response = client.post(
        "/voice", data={"text": "hello world", "mode": "transcribe_only"}
    )
    assert response.status_code == 200
    assert response.json() == {"text": "hello world", "transcript": "hello world"}


def test_execute_mode_calls_groq(client, monkeypatch):
    _mock_response(
        monkeypatch,
        _FakeResponse(200, {"choices": [{"message": {"content": "Hi there."}}]}),
    )
    response = client.post("/voice", data={"text": "say hi", "mode": "execute"})
    assert response.status_code == 200
    assert response.json() == {"transcript": "say hi", "response": "Hi there."}


def test_execute_mode_groq_error(client, monkeypatch):
    _mock_response(monkeypatch, _FakeResponse(500, {}))
    response = client.post("/voice", data={"text": "say hi", "mode": "execute"})
    assert response.status_code == 502
    assert "Groq LLM HTTP 500" in response.json()["error"]


def test_stt_mode_with_audio(client, monkeypatch):
    _mock_response(monkeypatch, _FakeResponse(200, {"text": " dictated words "}))
    # Synthetic payload is intentionally large enough to pass the size guard.
    fake_audio = b"RIFF" + b"\x00" * 3000
    response = client.post(
        "/voice",
        files={"audio": ("audio.wav", fake_audio, "audio/wav")},
        data={"mode": "transcribe_only"},
    )
    assert response.status_code == 200
    assert response.json() == {"text": "dictated words", "transcript": "dictated words"}


def test_stt_groq_error(client, monkeypatch):
    _mock_response(monkeypatch, _FakeResponse(500, {}))
    fake_audio = b"RIFF" + b"\x00" * 3000
    response = client.post(
        "/voice",
        files={"audio": ("audio.wav", fake_audio, "audio/wav")},
        data={"mode": "transcribe_only"},
    )
    assert response.status_code == 502
    assert "Groq STT HTTP 500" in response.json()["error"]


def test_unknown_mode_fails_closed_to_transcription(client, monkeypatch):
    def unexpected_post(*args, **kwargs):
        pytest.fail("unknown mode must not make an outbound LLM request")

    class _NoNetworkClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            unexpected_post(*args, **kwargs)

    monkeypatch.setattr(vb.httpx, "AsyncClient", _NoNetworkClient)
    response = client.post("/voice", data={"text": "hello", "mode": "transcribe"})
    assert response.status_code == 200
    assert response.json() == {"text": "hello", "transcript": "hello"}


def test_empty_transcript_returns_400(client):
    response = client.post("/voice", data={"text": "   ", "mode": "execute"})
    assert response.status_code == 400
    assert "empty transcript" in response.json()["error"]
