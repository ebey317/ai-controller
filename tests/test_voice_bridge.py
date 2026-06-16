"""Tests for the voice bridge."""
import os
import sys
from pathlib import Path

# Use the project venv packages and make voice_bridge importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ.setdefault("VOICE_BRIDGE_API_KEY", "")

import voice_bridge as vb
from fastapi.testclient import TestClient


class _FakeResponse:
    def __init__(self, status_code: int = 200, json_data: dict | None = None):
        self.status_code = status_code
        self._json = json_data or {}

    def json(self) -> dict:
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=None,
                response=self,
            )


def _set_mock_response(response: _FakeResponse) -> None:
    """Patch voice_bridge's httpx.AsyncClient so all posts return `response`."""
    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return response

    vb.httpx.AsyncClient = _FakeClient


client = TestClient(vb.app)
# Prevent the bridge from trying to speak during execute-mode tests.
vb._speak = lambda text: None


def test_transcribe_only_with_text():
    r = client.post("/voice", data={"text": "hello world", "mode": "transcribe_only"})
    assert r.status_code == 200
    assert r.json()["text"] == "hello world"


def test_execute_mode_calls_claf():
    _set_mock_response(_FakeResponse(200, {
        "content": [{"type": "text", "text": "Hi there."}]
    }))
    r = client.post("/voice", data={"text": "say hi", "mode": "execute"})
    assert r.status_code == 200
    body = r.json()
    assert body["transcript"] == "say hi"
    assert body["response"] == "Hi there."


def test_execute_mode_claf_error():
    _set_mock_response(_FakeResponse(500, {}))
    r = client.post("/voice", data={"text": "say hi", "mode": "execute"})
    assert r.status_code == 502
    assert "CLAF HTTP 500" in r.json()["error"]


def test_stt_mode_with_audio():
    _set_mock_response(_FakeResponse(200, {"text": " dictated words "}))
    # Fake WAV payload large enough to pass the 2000-byte check.
    fake_audio = b"RIFF" + b"\x00" * 3000
    r = client.post(
        "/voice",
        files={"audio": ("audio.wav", fake_audio, "audio/wav")},
        data={"mode": "transcribe_only"},
    )
    assert r.status_code == 200
    assert r.json()["text"] == "dictated words"


def test_stt_groq_error():
    _set_mock_response(_FakeResponse(500, {}))
    fake_audio = b"RIFF" + b"\x00" * 3000
    r = client.post(
        "/voice",
        files={"audio": ("audio.wav", fake_audio, "audio/wav")},
        data={"mode": "transcribe_only"},
    )
    assert r.status_code == 502
    assert "Groq STT HTTP 500" in r.json()["error"]


def test_empty_transcript_returns_400():
    r = client.post("/voice", data={"text": "   ", "mode": "execute"})
    assert r.status_code == 400
    assert "empty transcript" in r.json()["error"]


if __name__ == "__main__":
    test_transcribe_only_with_text()
    test_execute_mode_calls_claf()
    test_execute_mode_claf_error()
    test_stt_mode_with_audio()
    test_stt_groq_error()
    test_empty_transcript_returns_400()
    print("ok")
