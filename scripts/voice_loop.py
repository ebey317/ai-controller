#!/usr/bin/env python3
"""Tahuti voice loop v1 -- the real-time conversation cycle.

The loop is the missing mechanics between a push-to-talk press and a
reply that feels spoken rather than read aloud:

    idle -> listening -> thinking -> speaking -> done -> idle
                         \-> working ------------/

* Reply text is split into short phrases. Each phrase is synthesized
  and played before the next one is rendered, so speech starts with
  the first clause instead of waiting for a whole paragraph render.
* Playback is barge-in aware. The controller stack already uses
  /tmp/ai_tts_barge as its interruption tombstone (ptt_pynput touches
  it on a trigger press; voice_bridge checks its mtime). The loop
  uses that same file, plus an in-process request_barge_in() method
  and a CLI command, so an L/R handler can stop speech mid-phrase and
  return the loop to listening.
* Every transition is written atomically to
  ~/.config/ai-controller/loop-state.json. The Tahuti overlay can
  poll that file; the payload uses the avatar lifecycle vocabulary
  from voice_profiles.py (show_on_talk, stay_while_working,
  fade_when_done, popup_on_approval) instead of inventing new words.

Speech engines are profile-driven. The demo bridge is Microsoft Edge
TTS (profile engine "edge"; the historical spelling "edge-tts" is an
alias): edge-tts renders an MP3 phrase, then the SAME voice_fx chain
used by every other engine applies pitch, bass/treble EQ, and gain
before playback, so the voice-board knobs stay real. Kokoro and Piper
remain supported through controller_setup.speak_preview as a
phrase-at-a-time fallback; Tahuti's locked Kokoro default is not
changed by this module.

STT uses the existing Master AI server on port 8080: POST /stt with
raw audio bytes returns {"text": ...}. A non-browser client sends no
Origin header, so the server's extension-token branch is not used.
No parallel STT stack is installed or required.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import voice_profiles as vp  # noqa: E402
from ai_controller_paths import ai_controller_dir, config_dir  # noqa: E402

STATES = ("idle", "listening", "thinking", "speaking", "working", "done")

# Explicit turn-taking graph. Staying in the same state is always
# allowed (it is how phrase counters and details update).
TRANSITIONS = {
    "idle": {"listening", "working"},
    "listening": {"thinking", "working", "idle"},
    "thinking": {"speaking", "working", "listening", "done"},
    "speaking": {"listening", "working", "done", "idle"},
    "working": {"speaking", "listening", "done", "idle"},
    "done": {"idle", "listening", "working"},
}

DEFAULT_STATE_PATH = os.path.join(config_dir(), "loop-state.json")
DEFAULT_BARGE_FLAG = "/tmp/ai_tts_barge"
DEFAULT_STT_URL = "http://127.0.0.1:8080"

_SENTENCE_RE = re.compile(r"[^.!?;\n]+[.!?;]?")
_CLAUSE_BREAKS = (",", ";", ":", " — ", " – ", " - ")


def state_path() -> str:
    return os.path.expanduser(
        os.environ.get("AI_CONTROLLER_LOOP_STATE", DEFAULT_STATE_PATH))


def barge_flag_path() -> str:
    return os.path.expanduser(
        os.environ.get("AI_CONTROLLER_BARGE_FLAG", DEFAULT_BARGE_FLAG))


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(
        timespec="seconds")


def _atomic_write_json(path, payload) -> str:
    path = os.path.expanduser(path)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".loop-state-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def chunk_text(text, max_chars=140) -> list:
    """Split reply text into short speakable phrases.

    Sentences are kept whole when they fit. Over-long sentences are
    split at clause punctuation first and at word boundaries last;
    no phrase exceeds max_chars except a single unbreakable word.
    """
    if max_chars < 20:
        raise ValueError("max_chars must be at least 20")
    phrases = []
    for raw in str(text or "").splitlines():
        for match in _SENTENCE_RE.findall(raw):
            sentence = " ".join(match.strip().split())
            if not sentence:
                continue
            while len(sentence) > max_chars:
                cut = -1
                window = sentence[:max_chars + 1]
                for mark in _CLAUSE_BREAKS:
                    idx = window.rfind(mark)
                    if idx >= max_chars // 3:
                        cut = idx + len(mark)
                        break
                if cut < 0:
                    cut = window.rfind(" ")
                if cut < max_chars // 4:
                    cut = max_chars
                phrases.append(sentence[:cut].strip())
                sentence = sentence[cut:].strip()
            if sentence:
                phrases.append(sentence)
    return phrases


def _edge_python() -> str:
    candidates = [
        os.environ.get("AI_CONTROLLER_EDGE_PYTHON", ""),
        # The live controller venv is the stack voice_bridge runs on;
        # its edge-tts build is the one verified against Microsoft's
        # current service. Prefer it from either tree.
        os.path.expanduser("~/ai-controller/.venv/bin/python3"),
        os.path.join(ai_controller_dir(), ".venv", "bin", "python3"),
        os.path.join(ai_controller_dir(), ".venv", "bin", "python"),
        sys.executable,
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return sys.executable


def edge_voice_names(python=None, runner=subprocess.run,
                     timeout=30) -> list:
    """Voice short names from the installed edge-tts package."""
    python = python or _edge_python()
    result = runner(
        [python, "-m", "edge_tts", "--list-voices"],
        capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(
            "edge-tts --list-voices failed: "
            + (result.stderr or "")[-200:])
    names = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if parts and parts[0].endswith("Neural"):
            names.append(parts[0])
    return names


def _apply_profile_fx(mp3_path, wav_path, profile) -> str:
    """Decode an Edge MP3 and run the shared voice_fx chain."""
    import soundfile as sf

    import voice_fx

    samples, sample_rate = sf.read(
        mp3_path, dtype="float32", always_2d=False)
    knobs = vp.knob_values(profile)
    out = voice_fx.apply_fx(
        samples,
        sample_rate,
        pitch_semitones=float(profile.get("pitch_semitones", 0.0)),
        bass_db=knobs["bass_db"],
        treble_db=knobs["treble_db"],
        gain=knobs["gain"],
    )
    sf.write(wav_path, out, sample_rate)
    return wav_path


def render_edge_profile(text, profile, out_path,
                        runner=subprocess.run) -> str:
    """Render one phrase with Edge TTS, then voice_fx, to a WAV."""
    out_path = os.path.expanduser(out_path)
    mp3_path = out_path + ".mp3"
    rate = float(profile.get("rate", 1.0))
    result = runner(
        [_edge_python(), "-m", "edge_tts",
         "--voice", profile.get("voice", "en-US-ChristopherNeural"),
         f"--rate={round((rate - 1.0) * 100):+d}%",
         "--pitch=+0Hz",
         "--text", text,
         "--write-media", mp3_path],
        capture_output=True, text=True, timeout=60)
    if result.returncode != 0 or not os.path.exists(mp3_path) \
            or os.path.getsize(mp3_path) == 0:
        raise RuntimeError(
            "edge-tts render failed: " + (result.stderr or "")[-300:])
    try:
        return _apply_profile_fx(mp3_path, out_path, profile)
    finally:
        try:
            os.unlink(mp3_path)
        except OSError:
            pass


def _default_player(path):
    if shutil.which("mpv"):
        return subprocess.Popen(
            ["mpv", "--no-video", "--force-media-title=AI_TTS_BARGE",
             path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if shutil.which("aplay"):
        return subprocess.Popen(
            ["aplay", "-q", path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    raise RuntimeError("no audio player found (mpv/aplay)")


class STTError(RuntimeError):
    pass


class STTClient:
    """Thin client for the existing Master AI /stt endpoint."""

    def __init__(self, base_url=DEFAULT_STT_URL, timeout=120,
                 urlopen=urllib.request.urlopen):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._urlopen = urlopen

    def transcribe(self, audio_bytes, content_type="audio/webm") -> str:
        if not audio_bytes:
            raise STTError("no audio bytes to transcribe")
        request = urllib.request.Request(
            self.base_url + "/stt",
            data=audio_bytes,
            headers={"Content-Type": content_type},
            method="POST")
        try:
            with self._urlopen(request, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:300]
            raise STTError(f"/stt returned HTTP {e.code}: {detail}") \
                from e
        except Exception as e:
            raise STTError(f"/stt request failed: {e}") from e
        if payload.get("error"):
            raise STTError(f"/stt error: {payload['error']}")
        return str(payload.get("text", "")).strip()

    def transcribe_file(self, path, content_type="audio/webm") -> str:
        with open(path, "rb") as f:
            return self.transcribe(f.read(), content_type=content_type)


def signal_barge_in(state_file=None, flag_file=None,
                    source="lr") -> bool:
    """File-level barge-in for an L/R handler in another process.

    Touches the shared barge tombstone and, when the recorded loop
    state is speaking, rewrites it as listening. Returns True when a
    speaking state was interrupted.
    """
    state_file = state_file or state_path()
    flag_file = flag_file or barge_flag_path()
    directory = os.path.dirname(os.path.expanduser(flag_file))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(flag_file, "w", encoding="utf-8") as f:
        f.write(str(source))
    payload = None
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError):
        payload = None
    if not payload or payload.get("state") != "speaking":
        return False
    payload["previous_state"] = "speaking"
    payload["state"] = "listening"
    payload["updated_at"] = _now_iso()
    payload.setdefault("detail", {})["reason"] = "barge_in"
    payload["detail"]["barge_source"] = str(source)
    avatar = payload.setdefault("avatar", {})
    avatar["visible"] = True
    _atomic_write_json(state_file, payload)
    return True


class VoiceLoop:
    """One conversation cycle, with pollable state and barge-in."""

    def __init__(self, profile_name="tahuti_demo", profile=None,
                 profiles_data=None, profiles_path=None,
                 state_file=None, flag_file=None, synthesizer=None,
                 player_factory=None, stt_client=None,
                 poll_interval=0.02):
        self.state_file = state_file or state_path()
        self.flag_file = flag_file or barge_flag_path()
        if profiles_data is None:
            profiles_data = vp.load_profiles(profiles_path)
        self.profiles_data = profiles_data
        self.profile_name = profile_name
        self.profile = profile or vp.get_avatar_profile(
            profiles_data, profile_name)
        self.settings = vp.get_avatar_settings(profiles_data)
        self.synthesizer = synthesizer or self._default_synthesizer
        self.player_factory = player_factory or _default_player
        self.stt_client = stt_client or STTClient()
        self.poll_interval = poll_interval
        self.state = "idle"
        self.previous_state = None
        self.detail = {"reason": "init"}
        self._current_player = None
        self._barge_event = threading.Event()
        self._stream_started_at = 0.0
        self._barge_flag_baseline = None
        self._write_state()

    # ── state ────────────────────────────────────────────────────

    def _avatar_visible(self, state) -> bool:
        if state in ("listening", "thinking", "speaking"):
            return bool(self.settings.get("show_on_talk", True))
        if state == "working":
            return bool(self.settings.get("stay_while_working", True))
        if state == "done":
            return not bool(self.settings.get("fade_when_done", True))
        return False

    def payload(self) -> dict:
        return {
            "state": self.state,
            "previous_state": self.previous_state,
            "element": "tahuti",
            "profile_name": self.profile_name,
            "engine": vp.canonical_engine(
                self.profile.get("engine", "")),
            "voice": self.profile.get("voice"),
            "avatar": {
                "active_avatar": self.settings.get("active_avatar"),
                "visible": self._avatar_visible(self.state),
                "show_on_talk": self.settings.get("show_on_talk"),
                "stay_while_working": self.settings.get(
                    "stay_while_working"),
                "fade_when_done": self.settings.get("fade_when_done"),
                "popup_on_approval": self.settings.get(
                    "popup_on_approval"),
                "result_supersedes": self.state == "done",
            },
            "detail": self.detail,
            "updated_at": _now_iso(),
        }

    def _write_state(self):
        _atomic_write_json(self.state_file, self.payload())

    def read_state(self) -> dict:
        with open(self.state_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def transition(self, new_state, **detail) -> str:
        if new_state not in STATES:
            raise ValueError(f"unknown loop state {new_state!r}")
        if new_state != self.state and \
                new_state not in TRANSITIONS[self.state]:
            raise ValueError(
                f"loop cannot go {self.state} -> {new_state}")
        self.previous_state = self.state
        self.state = new_state
        self.detail = detail or {"reason": "transition"}
        self._write_state()
        return self.state

    def begin_listening(self, **detail):
        return self.transition("listening", **(detail or {
            "reason": "talk_button"}))

    def begin_thinking(self, **detail):
        return self.transition("thinking", **(detail or {
            "reason": "reply_pending"}))

    def begin_working(self, **detail):
        return self.transition("working", **(detail or {
            "reason": "task_running"}))

    def finish(self, **detail):
        return self.transition("done", **(detail or {
            "reason": "result_ready", "result_ready": True}))

    def settle(self, **detail):
        """done -> idle: the result has landed; the avatar fades."""
        return self.transition("idle", **(detail or {
            "reason": "settled", "faded": bool(
                self.settings.get("fade_when_done", True))}))

    # ── barge-in ─────────────────────────────────────────────────

    def request_barge_in(self, source="lr") -> bool:
        """Stop current speech and return to listening.

        Safe to call from a signal handler or another thread in this
        process. Cross-process callers use signal_barge_in() or the
        CLI's barge-in command, which touch the same flag file.
        """
        self._barge_event.set()
        directory = os.path.dirname(os.path.expanduser(self.flag_file))
        if directory:
            os.makedirs(directory, exist_ok=True)
        try:
            with open(self.flag_file, "w", encoding="utf-8") as f:
                f.write(str(source))
        except OSError:
            pass
        was_speaking = self.state == "speaking"
        self._stop_current_player()
        if was_speaking:
            self.transition(
                "listening", reason="barge_in", barge_source=str(source))
        return was_speaking

    # The L/R handler's natural verb.
    barge_in = request_barge_in

    def _flag_stamp(self):
        try:
            st = os.stat(self.flag_file)
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _barge_requested(self) -> bool:
        if self._barge_event.is_set():
            return True
        if self._stream_started_at:
            # Change detection against the stamp taken when this
            # stream started -- NOT an mtime-vs-clock comparison.
            # Filesystem and wall clocks can disagree by a few ms,
            # which made a raw >= check flaky; a changed stamp is
            # unambiguous.
            stamp = self._flag_stamp()
            if stamp is not None and \
                    stamp != self._barge_flag_baseline:
                return True
        return False

    def _stop_current_player(self):
        player = self._current_player
        if player is None:
            return
        try:
            if player.poll() is None:
                player.terminate()
                try:
                    player.wait(timeout=2)
                except Exception:
                    player.kill()
        except Exception:
            pass

    # ── speech ───────────────────────────────────────────────────

    def _default_synthesizer(self, phrase, profile, out_path):
        if vp.canonical_engine(profile.get("engine", "")) == "edge":
            return render_edge_profile(phrase, profile, out_path), False
        # Kokoro/Piper fallback: controller_setup renders AND plays
        # one phrase through the same voice_fx chain. Phrase-at-a-time
        # keeps streaming; barge-in lands between phrases here, and
        # mid-phrase for the Edge path above.
        import controller_setup as cs

        ok = cs.speak_preview(profile, self.profile_name, text=phrase)
        if not ok:
            raise RuntimeError("phrase preview synthesis failed")
        return None, True

    def speak_reply(self, text, settle=False) -> dict:
        """Speak a reply phrase by phrase; barge-in returns early."""
        phrases = chunk_text(text)
        if not phrases:
            return {"ok": False, "interrupted": False, "phrases": 0,
                    "reason": "empty_text"}
        if self.state in ("idle", "done"):
            self.transition("listening", reason="reply_cycle")
        if self.state == "listening":
            self.transition("thinking", reason="reply_ready",
                            phrase_count=len(phrases))
        self._barge_event.clear()
        self._stream_started_at = time.time()
        self._barge_flag_baseline = self._flag_stamp()
        interrupted = False
        with tempfile.TemporaryDirectory(
                prefix="voice-loop-") as tmpdir:
            for index, phrase in enumerate(phrases):
                if self._barge_requested():
                    interrupted = True
                    break
                self.transition(
                    "speaking", reason="phrase", phrase_index=index,
                    phrase_count=len(phrases), phrase=phrase)
                out_path = os.path.join(
                    tmpdir, f"phrase-{index:03d}.wav")
                rendered, already_played = self.synthesizer(
                    phrase, self.profile, out_path)
                if self._barge_requested():
                    interrupted = True
                    break
                if already_played:
                    continue
                player = self.player_factory(rendered or out_path)
                self._current_player = player
                if self._barge_requested():
                    self._stop_current_player()
                    self._current_player = None
                    interrupted = True
                    break
                while player.poll() is None:
                    if self._barge_requested():
                        self._stop_current_player()
                        interrupted = True
                        break
                    time.sleep(self.poll_interval)
                self._current_player = None
                if interrupted or self._barge_requested():
                    interrupted = True
                    break
        if interrupted:
            if self.state != "listening":
                self.transition(
                    "listening", reason="barge_in",
                    phrase_count=len(phrases))
            return {"ok": False, "interrupted": True,
                    "phrases": len(phrases)}
        self.transition(
            "done", reason="result_ready", result_ready=True,
            phrase_count=len(phrases))
        if settle:
            self.settle()
        return {"ok": True, "interrupted": False,
                "phrases": len(phrases)}

    # ── listening / STT ──────────────────────────────────────────

    def transcribe_audio(self, audio_bytes,
                         content_type="audio/webm") -> str:
        """State-wrapped STT through the existing /stt endpoint."""
        if self.state in ("idle", "done", "speaking"):
            self.transition("listening", reason="audio_received")
        text = self.stt_client.transcribe(
            audio_bytes, content_type=content_type)
        self.transition("thinking", reason="transcribed",
                        transcript_chars=len(text))
        return text


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-file")
    parser.add_argument("--flag-file")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("state")
    p_set = sub.add_parser("set-state")
    p_set.add_argument("state", choices=STATES)
    p_barge = sub.add_parser("barge-in")
    p_barge.add_argument("--source", default="lr")
    p_speak = sub.add_parser("speak")
    p_speak.add_argument("--text", required=True)
    p_speak.add_argument("--profile", default="tahuti_demo")
    p_speak.add_argument("--settle", action="store_true")
    p_stt = sub.add_parser("transcribe")
    p_stt.add_argument("audio")
    args = parser.parse_args(argv)

    state_file = args.state_file or state_path()
    flag_file = args.flag_file or barge_flag_path()
    if args.cmd == "state":
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                print(f.read(), end="")
        except OSError:
            print(json.dumps({"state": "idle", "recorded": False}))
        return 0
    if args.cmd == "barge-in":
        hit = signal_barge_in(state_file, flag_file, args.source)
        print("interrupted speaking" if hit else "barge flag set")
        return 0
    loop = VoiceLoop(state_file=state_file, flag_file=flag_file,
                     profile_name=getattr(args, "profile", "tahuti_demo"))
    if args.cmd == "set-state":
        loop.transition(args.state, reason="cli")
        print(loop.state)
        return 0
    if args.cmd == "speak":
        result = loop.speak_reply(args.text, settle=args.settle)
        print(json.dumps(result))
        return 0 if result["ok"] else 2
    if args.cmd == "transcribe":
        with open(args.audio, "rb") as f:
            print(loop.transcribe_audio(f.read()))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
