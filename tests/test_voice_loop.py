"""Tests for voice_loop.py -- Tahuti's real-time conversation loop.

Hermetic: fake synthesizers, fake players, and a fake /stt opener.
Nothing here plays audio, touches the network, or reads the real
profile / state files.
"""

import json
import sys
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


# ── STT client (existing /stt on :8080) ──────────────────────────

class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def test_stt_client_posts_raw_audio_to_existing_endpoint():
    seen = {}

    def opener(request, timeout=None):
        seen["url"] = request.full_url
        seen["data"] = request.data
        seen["headers"] = dict(request.header_items())
        seen["timeout"] = timeout
        return _FakeResponse({"text": "open the record"})

    client = vl.STTClient(urlopen=opener)
    assert client.transcribe(b"webm-bytes") == "open the record"
    assert seen["url"] == "http://127.0.0.1:8080/stt"
    assert seen["data"] == b"webm-bytes"
    assert seen["headers"].get("Content-type") == "audio/webm"
    # No Origin: the server only demands its token from browser
    # extension origins; a local client uses the plain route.
    assert "Origin" not in seen["headers"]


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
