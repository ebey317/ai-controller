"""Tests for voice_loop.py -- Tahuti's real-time conversation loop.

Hermetic: fake synthesizers, fake players, and a fake STT opener.
Nothing here plays audio, touches the network, or reads the real
profile / state files.
"""

import io
import json
import sys
import urllib.error
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import voice_loop as vl  # noqa: E402
import voice_profiles as vp  # noqa: E402


def _data():
    return vp.load_profiles("/nonexistent/voice-profiles.json")


def _loop(tmp_path, **kwargs):
    kwargs.setdefault("profiles_data", _data())
    kwargs.setdefault("state_file", str(tmp_path / "loop-state.json"))
    kwargs.setdefault("flag_file", str(tmp_path / "barge"))
    return vl.VoiceLoop(**kwargs)


class DonePlayer:
    def __init__(self, path):
        self.path = path
        self.terminated = False

    def poll(self):
        return 0

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.terminated = True


class HangingPlayer(DonePlayer):
    def poll(self):
        return 0 if self.terminated else None


def _writer_synth(record):
    def synth(phrase, profile, out_path):
        record.append(phrase)
        Path(out_path).write_bytes(b"fake-wav")
        return out_path, False
    return synth


# ── phrase chunking ──────────────────────────────────────────────

def test_chunking_keeps_short_sentences_whole():
    text = "I am Tahuti. I keep the record. Nothing moves without you."
    assert vl.chunk_text(text) == [
        "I am Tahuti.", "I keep the record.",
        "Nothing moves without you."]


def test_chunking_splits_long_text_into_short_phrases():
    text = ("First we check the record, then we weigh the words, "
            "then we ask for approval, and only then does anything "
            "move across the desk.")
    phrases = vl.chunk_text(text, max_chars=60)
    assert len(phrases) > 1
    assert all(len(p) <= 60 for p in phrases)
    assert " ".join(phrases).split() == text.split()


def test_chunking_handles_questions_and_empty():
    assert vl.chunk_text("Ready? Yes! Let us begin.") == [
        "Ready?", "Yes!", "Let us begin."]
    assert vl.chunk_text("") == []
    assert vl.chunk_text("   \n  ") == []


# ── state machine ────────────────────────────────────────────────

def test_state_transitions_follow_the_turn_cycle(tmp_path):
    loop = _loop(tmp_path)
    assert loop.read_state()["state"] == "idle"
    loop.begin_listening()
    loop.begin_thinking()
    loop.begin_working()
    assert loop.state == "working"
    loop.transition("speaking")
    loop.finish()
    payload = loop.read_state()
    assert payload["state"] == "done"
    # The lifecycle words ride along for the overlay.
    assert payload["avatar"]["result_supersedes"] is True
    assert payload["avatar"]["visible"] is False  # fade_when_done
    assert payload["avatar"]["show_on_talk"] is True
    assert payload["avatar"]["stay_while_working"] is True
    assert payload["avatar"]["popup_on_approval"] is True
    loop.settle()
    assert loop.state == "idle"


def test_state_machine_rejects_impossible_jump(tmp_path):
    loop = _loop(tmp_path)
    with pytest.raises(ValueError):
        loop.transition("speaking")  # idle cannot speak before listening


def test_state_file_writes_are_complete_json(tmp_path):
    loop = _loop(tmp_path)
    loop.begin_listening()
    payload = json.loads((tmp_path / "loop-state.json").read_text())
    assert payload["state"] == "listening"
    assert payload["element"] == "tahuti"
    assert payload["profile_name"] == "tahuti_demo"


# ── streamed speech + barge-in ───────────────────────────────────

def test_speak_reply_streams_phrase_by_phrase(tmp_path):
    spoken, played = [], []
    loop = _loop(
        tmp_path,
        synthesizer=_writer_synth(spoken),
        player_factory=lambda p: played.append(p) or DonePlayer(p))
    result = loop.speak_reply("One short line. A second line follows.")
    assert result == {"ok": True, "interrupted": False, "phrases": 2}
    assert spoken == ["One short line.", "A second line follows."]
    assert len(played) == 2  # each phrase played before the next render
    assert loop.state == "done"


def test_barge_in_from_speaking_returns_to_listening(tmp_path):
    holder = {}

    def factory(path):
        player = HangingPlayer(path)
        holder["player"] = player
        holder["loop"].request_barge_in(source="test")
        return player

    loop = _loop(tmp_path, synthesizer=_writer_synth([]),
                 player_factory=factory)
    holder["loop"] = loop
    result = loop.speak_reply("A long reply that should be cut off. "
                              "And more after it.")
    assert result["interrupted"] is True
    assert holder["player"].terminated is True
    assert loop.state == "listening"
    assert loop.read_state()["state"] == "listening"


def test_external_barge_flag_stops_the_stream(tmp_path):
    flag = tmp_path / "barge"

    def synth(phrase, profile, out_path):
        # The L/R handler in another process touches the tombstone.
        flag.write_text("lr")
        Path(out_path).write_bytes(b"fake-wav")
        return out_path, False

    played = []
    loop = _loop(tmp_path, synthesizer=synth,
                 player_factory=lambda p: played.append(p)
                 or DonePlayer(p))
    result = loop.speak_reply("This should never reach the speaker.")
    assert result["interrupted"] is True
    assert played == []
    assert loop.state == "listening"


def test_signal_barge_in_rewrites_a_speaking_state_file(tmp_path):
    loop = _loop(tmp_path, synthesizer=_writer_synth([]),
                 player_factory=lambda p: DonePlayer(p))
    loop.begin_listening()
    loop.begin_thinking()
    loop.transition("speaking")
    hit = vl.signal_barge_in(str(tmp_path / "loop-state.json"),
                             str(tmp_path / "barge"), source="lr")
    assert hit is True
    assert loop.read_state()["state"] == "listening"


# ── STT client (Groq Whisper -- the voice_bridge route) ─────────

_FAKE_GROQ_KEY = "gsk_test_" + "x" * 30


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _capturing_opener(payload, seen):
    def opener(request, timeout=None):
        seen["url"] = request.full_url
        seen["data"] = request.data
        seen["headers"] = dict(request.header_items())
        seen["timeout"] = timeout
        return _FakeResponse(payload)
    return opener


def test_stt_client_posts_multipart_to_the_groq_route():
    seen = {}
    client = vl.STTClient(
        api_key=_FAKE_GROQ_KEY,
        urlopen=_capturing_opener({"text": "  open the record  "}, seen))
    assert client.transcribe(b"wav-bytes",
                             content_type="audio/wav") == "open the record"
    # Same endpoint, auth shape, model, and prompt bias as
    # voice_bridge.py -- the stack's production STT route.
    assert seen["url"] == vl.GROQ_STT_URL
    assert seen["url"] == \
        "https://api.groq.com/openai/v1/audio/transcriptions"
    assert seen["headers"].get("Authorization") == \
        f"Bearer {_FAKE_GROQ_KEY}"
    # Cloudflare rejects urllib's default agent (403/1010); the
    # client must declare its own, like the bridge's httpx does.
    assert seen["headers"].get("User-agent") == vl.GROQ_USER_AGENT
    assert seen["headers"].get("Content-type", "").startswith(
        "multipart/form-data; boundary=")
    body = seen["data"]
    assert b'name="model"' in body
    assert b"whisper-large-v3-turbo" in body
    assert b'name="prompt"' in body
    assert b"Madam Mary" in body and b"Sensei" in body
    assert b'name="file"; filename="audio.wav"' in body
    assert b"Content-Type: audio/wav" in body
    assert b"wav-bytes" in body
    assert seen["timeout"] == 30


def test_stt_client_names_the_file_for_its_content_type():
    seen = {}
    client = vl.STTClient(
        api_key=_FAKE_GROQ_KEY,
        urlopen=_capturing_opener({"text": "ok"}, seen))
    client.transcribe(b"clip")
    assert b'filename="audio.webm"' in seen["data"]
    client.transcribe(b"clip", content_type="audio/mpeg")
    assert b'filename="audio.mp3"' in seen["data"]


def test_stt_client_truncates_long_transcripts_like_the_bridge():
    client = vl.STTClient(
        api_key=_FAKE_GROQ_KEY,
        urlopen=_capturing_opener({"text": "x" * 3000}, {}))
    assert len(client.transcribe(b"clip")) == vl.MAX_TRANSCRIPT_CHARS


def test_stt_client_http_error_raises_stt_error():
    def opener(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 401, "Unauthorized", None,
            io.BytesIO(b'{"error": "bad key"}'))

    client = vl.STTClient(api_key=_FAKE_GROQ_KEY, urlopen=opener)
    with pytest.raises(vl.STTError, match="Groq STT HTTP 401"):
        client.transcribe(b"clip")


def test_stt_client_network_error_raises_stt_error():
    def opener(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    client = vl.STTClient(api_key=_FAKE_GROQ_KEY, urlopen=opener)
    with pytest.raises(vl.STTError, match="Groq STT request failed"):
        client.transcribe(b"clip")


def test_stt_client_error_payload_raises_stt_error():
    client = vl.STTClient(
        api_key=_FAKE_GROQ_KEY,
        urlopen=_capturing_opener({"error": {"message": "bad audio"}}, {}))
    with pytest.raises(vl.STTError, match="Groq STT error"):
        client.transcribe(b"clip")


def test_stt_client_without_a_key_fails_closed():
    def opener(request, timeout=None):  # must never be reached
        raise AssertionError("network touched without a key")

    client = vl.STTClient(api_key="", urlopen=opener)
    with pytest.raises(vl.STTError, match="GROQ_API_KEY"):
        client.transcribe(b"clip")


def test_groq_key_loader_mirrors_the_bridge_sources(monkeypatch):
    # Environment first, with the bridge's unmasked/length guard.
    monkeypatch.setenv("GROQ_API_KEY", _FAKE_GROQ_KEY)
    assert vl._load_groq_key() == _FAKE_GROQ_KEY
    monkeypatch.setenv("GROQ_API_KEY", "***masked***")
    monkeypatch.setattr(vl, "load_env",
                        lambda: {"GROQ_API_KEY": _FAKE_GROQ_KEY})
    assert vl._load_groq_key() == _FAKE_GROQ_KEY


def test_default_stt_client_is_groq_unless_local_opt_in(monkeypatch):
    monkeypatch.delenv("AI_CONTROLLER_STT", raising=False)
    monkeypatch.setattr(vl, "_load_groq_key", lambda: _FAKE_GROQ_KEY)
    assert isinstance(vl.default_stt_client(), vl.STTClient)
    monkeypatch.setenv("AI_CONTROLLER_STT", "local")
    assert isinstance(vl.default_stt_client(), vl.LocalSTTClient)


def test_local_stt_client_posts_raw_audio_to_the_old_endpoint():
    # The labeled non-default fallback keeps its original behavior.
    seen = {}
    client = vl.LocalSTTClient(
        urlopen=_capturing_opener({"text": "open the record"}, seen))
    assert client.transcribe(b"webm-bytes") == "open the record"
    assert seen["url"] == "http://127.0.0.1:8080/stt"
    assert seen["data"] == b"webm-bytes"
    assert seen["headers"].get("Content-type") == "audio/webm"


def test_loop_transcribe_moves_listening_to_thinking(tmp_path):
    class FakeSTT:
        def transcribe(self, audio, content_type="audio/webm"):
            assert audio == b"clip"
            return "check my messages"

    loop = _loop(tmp_path, stt_client=FakeSTT())
    assert loop.transcribe_audio(b"clip") == "check my messages"
    assert loop.state == "thinking"


# ── Edge demo bridge ─────────────────────────────────────────────

def test_edge_voice_names_parse_the_installed_catalog():
    def runner(argv, capture_output, text, timeout):
        return SimpleNamespace(
            returncode=0, stderr="",
            stdout=("en-US-ChristopherNeural  Male   News, Novel\n"
                    "en-US-AriaNeural       Female News, Novel\n"))

    names = vl.edge_voice_names(python="python3", runner=runner)
    assert names == ["en-US-ChristopherNeural", "en-US-AriaNeural"]


def test_render_edge_profile_runs_fx_after_synthesis(tmp_path):
    np = pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")
    sr = 24000
    tone = (0.4 * np.sin(2 * np.pi * 220 *
                         (np.arange(sr // 2) / sr))).astype("float32")
    seen = {}

    def runner(argv, capture_output, text, timeout):
        seen["argv"] = argv
        mp3_path = argv[-1]
        # edge-tts writes an MP3; stand in with WAV bytes -- the FX
        # decoder sniffs the real format, and what this test pins is
        # the chain AFTER synthesis: decode -> voice_fx -> WAV out.
        with wave.open(mp3_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes((tone * 32767).astype("<i2").tobytes())
        return SimpleNamespace(returncode=0, stderr="")

    profile = {"engine": "edge", "voice": "en-US-ChristopherNeural",
               "rate": 1.07, "pitch_semitones": 0.0, "gain": 0.5}
    out = str(tmp_path / "phrase.wav")
    assert vl.render_edge_profile("Hello.", profile, out,
                                  runner=runner) == out
    assert seen["argv"][seen["argv"].index("--voice") + 1] == \
        "en-US-ChristopherNeural"
    assert "--rate=+7%" in seen["argv"]
    samples, _ = sf.read(out, dtype="float32")
    assert float(np.max(np.abs(samples))) == pytest.approx(0.2, abs=0.03)
