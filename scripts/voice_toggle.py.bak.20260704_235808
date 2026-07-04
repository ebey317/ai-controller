#!/usr/bin/env python3
"""
Toggle the active AI controller TTS voice between available Piper voice packs.
State is stored in ~/.config/ai_controller_voice.
Can be triggered from the keyboard, a controller button, or the command line.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

import numpy as np
from scipy.io import wavfile

# Make shared helpers available regardless of cwd.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
from ai_controller_paths import ai_controller_dir, config_dir, ensure_config_dir

ensure_config_dir()
STATE_FILE = os.path.join(config_dir(), "ai_controller_voice")
UNLOCKS_FILE = os.path.join(config_dir(), "ai_controller_unlocked_voices.json")
VOICES_DIR = os.path.join(ai_controller_dir(), "voices")

_DEVNULL = subprocess.DEVNULL


def _discover_voices():
    """Find all voice packs under voices/ with valid config."""
    voices = []
    if not os.path.isdir(VOICES_DIR):
        return voices
    for voice_id in sorted(os.listdir(VOICES_DIR)):
        voice_path = os.path.join(VOICES_DIR, voice_id)
        if not os.path.isdir(voice_path):
            continue
        config_path = os.path.join(voice_path, "config.json")
        if not os.path.exists(config_path):
            continue
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            continue
        engine = cfg.get("engine", "piper")
        # find any .onnx model in the pack (locked placeholders may ship without one)
        model_file = None
        if engine == "piper":
            onnx_files = [n for n in os.listdir(voice_path) if n.endswith(".onnx")]
            if onnx_files:
                model_file = onnx_files[0]
            if cfg.get("model"):
                candidate = cfg["model"]
                if os.path.exists(os.path.join(voice_path, candidate)):
                    model_file = candidate
            if model_file is None and not cfg.get("locked"):
                # base/free Piper voices must have a working model
                continue
        voices.append({
            "id": voice_id,
            "name": cfg.get("name", voice_id),
            "label": cfg.get("label", voice_id.upper()),
            "engine": engine,
            "voice": cfg.get("voice", ""),
            "pitch": cfg.get("pitch", "+0Hz"),
            "rate": cfg.get("rate", "+0%"),
            "locked": bool(cfg.get("locked", False)),
            "price": cfg.get("price", ""),
            "description": cfg.get("description", ""),
            "model": os.path.join(voice_path, model_file) if model_file else None,
        })
    return voices


def _load_unlocks():
    try:
        with open(UNLOCKS_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def _save_unlocks(unlocks):
    os.makedirs(os.path.dirname(UNLOCKS_FILE), exist_ok=True)
    with open(UNLOCKS_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(unlocks), f, indent=2)


def is_unlocked(voice_id):
    voice = get_voice(voice_id)
    if not voice or not voice["locked"]:
        return True
    return voice_id in _load_unlocks()


def unlock(voice_id):
    unlocks = _load_unlocks()
    unlocks.add(voice_id)
    _save_unlocks(unlocks)


def get_voice(voice_id):
    for v in _discover_voices():
        if v["id"] == voice_id:
            return v
    return None


def load_voice():
    """Return the active voice ID, falling back to the first unlocked voice."""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            active = f.read().strip().lower()
    except Exception:
        active = ""
    voices = _discover_voices()
    unlocked = [v for v in voices if is_unlocked(v["id"])]
    if active and any(v["id"] == active for v in unlocked):
        return active
    if unlocked:
        return unlocked[0]["id"]
    if voices:
        return voices[0]["id"]
    return "joe"


def save_voice(voice_id):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        f.write(voice_id)


def list_voices():
    """Return all discovered voices with unlock status."""
    voices = _discover_voices()
    unlocks = _load_unlocks()
    for v in voices:
        v["unlocked"] = not v["locked"] or v["id"] in unlocks
    return voices


def _split_sentences(text):
    """Split text into sentence-sized chunks for back-end spacing."""
    chunks = re.split(r'(?<=[.!?])\s+', text.strip())
    return [c for c in chunks if c]


def _synthesize_sentence(model_path, sentence, output_path):
    """Generate one sentence with Piper using the stock model config."""
    subprocess.run(
        ["piper", "--model", model_path,
         "--output_file", output_path],
        input=sentence.encode(), check=False,
        stdout=_DEVNULL, stderr=_DEVNULL
    )


def _concat_with_pauses(paths, pause_ms, output_path):
    """Concatenate WAV files with silence between them."""
    segments = []
    rate = None
    for p in paths:
        try:
            r, data = wavfile.read(p)
        except Exception:
            continue
        if rate is None:
            rate = r
        if data.ndim > 1:
            data = data[:, 0]
        segments.append(data)
        # silence between sentences
        silence = np.zeros(int(rate * pause_ms / 1000), dtype=data.dtype)
        segments.append(silence)
    if not segments or rate is None:
        return
    # Drop trailing silence
    combined = np.concatenate(segments[:-1]) if len(segments) > 1 else segments[0]
    wavfile.write(output_path, rate, combined)


def _speak_edge(text, voice):
    """Speak text using Microsoft Edge TTS."""
    mp3_fd, mp3_path = tempfile.mkstemp(suffix=".mp3", prefix="tts_")
    os.close(mp3_fd)
    try:
        subprocess.run(
            [sys.executable, "-m", "edge_tts",
             "--voice", voice.get("voice", "en-US-AriaNeural"),
             "--pitch=" + voice.get("pitch", "+0Hz"),
             "--rate=" + voice.get("rate", "+0%"),
             "--text", text,
             "--write-media", mp3_path],
            stdout=_DEVNULL, stderr=_DEVNULL, check=True, timeout=30
        )
        subprocess.run(["mpv", "--no-video", mp3_path],
                       check=False, stdout=_DEVNULL, stderr=_DEVNULL)
        return True
    except Exception:
        return False
    finally:
        try:
            os.unlink(mp3_path)
        except Exception:
            pass


def _speak_spd(text):
    """Ultimate fallback using the system's speech-dispatcher voice."""
    subprocess.run(["spd-say", "-w", text[:500]],
                   stdout=_DEVNULL, stderr=_DEVNULL)


def _piper_speak(text, model_path):
    """Try to speak with Piper; return True if audio was produced."""
    if not model_path or not os.path.isfile(model_path):
        return False
    sentences = _split_sentences(text)
    if not sentences:
        return False

    tmp_dir = tempfile.mkdtemp(prefix="piper_sentences_")
    try:
        paths = []
        for i, sentence in enumerate(sentences):
            out = os.path.join(tmp_dir, f"sent_{i:03d}.wav")
            _synthesize_sentence(model_path, sentence, out)
            if os.path.exists(out) and os.path.getsize(out) > 0:
                paths.append(out)
        if paths:
            _concat_with_pauses(paths, pause_ms=180, output_path="/tmp/ai_controller_tts.wav")
    finally:
        for f in os.listdir(tmp_dir):
            try:
                os.unlink(os.path.join(tmp_dir, f))
            except Exception:
                pass
        os.rmdir(tmp_dir)

    output_wav = "/tmp/ai_controller_tts.wav"
    if not os.path.exists(output_wav) or os.path.getsize(output_wav) == 0:
        return False

    subprocess.run(["mpv", "--no-video", "--speed=0.97", output_wav],
                   check=False, stdout=_DEVNULL, stderr=_DEVNULL)
    return True


def speak(text, voice_id=None):
    voice_id = voice_id or load_voice()
    voice = get_voice(voice_id)
    if voice is None:
        # fallback: try legacy hardcoded Joe path
        voice = {
            "id": "joe",
            "engine": "piper",
            "model": os.path.join(VOICES_DIR, "joe", "en_US-joe-medium.onnx"),
        }

    if voice.get("engine") == "edge-tts":
        if _speak_edge(text, voice):
            return
        _speak_spd(text)
        return

    model_path = voice.get("model")
    if _piper_speak(text, model_path):
        return

    # Piper failed or model missing — fall back to cloud TTS, then spd-say.
    if _speak_edge(text, {"voice": "en-US-AriaNeural", "pitch": "+0Hz", "rate": "+0%"}):
        return
    _speak_spd(text)


def toggle():
    voices = list_voices()
    unlocked = [v for v in voices if v["unlocked"]]
    if not unlocked:
        return None
    current = load_voice()
    ids = [v["id"] for v in unlocked]
    try:
        idx = ids.index(current)
    except ValueError:
        idx = -1
    next_voice = ids[(idx + 1) % len(ids)]
    save_voice(next_voice)
    return next_voice


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--speak":
        text = " ".join(sys.argv[2:]) or "Voice ready."
        speak(text)
    elif len(sys.argv) > 1 and sys.argv[1] == "--list":
        for v in list_voices():
            status = "UNLOCKED" if v["unlocked"] else "LOCKED"
            print(f"{v['id']}: {v['name']} [{status}]")
    elif len(sys.argv) > 2 and sys.argv[1] == "--unlock":
        unlock(sys.argv[2])
        print(f"Unlocked {sys.argv[2]}.")
    else:
        new_voice = toggle()
        if new_voice:
            print(new_voice)
            speak(f"Switched to {new_voice}.", voice_id=new_voice)
        else:
            print("No voices available.")
