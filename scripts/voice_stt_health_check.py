#!/usr/bin/env python3
"""
voice_stt_health_check.py — verify the free voice + STT stack is ready to run.

Checks:
- Voice packs are installed and have model/config files
- Active voice can speak
- voice-bridge service is reachable
- Groq API key is configured
- STT pipeline can transcribe a test WAV
"""
import json
import os
import subprocess
import sys
import tempfile
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import voice_toggle
from ai_controller_paths import ai_controller_dir, config_dir, load_env

VOICE_BRIDGE_URL = os.environ.get("VOICE_BRIDGE_URL", "http://localhost:8002/voice")

def _ok(msg):
    print(f"  ✅ {msg}")

def _fail(msg):
    print(f"  ❌ {msg}")
    return False

def _warn(msg):
    print(f"  ⚠️  {msg}")


def check_voices():
    print("Checking voice packs...")
    voices = voice_toggle.list_voices()
    unlocked = [v for v in voices if v["unlocked"]]
    if not voices:
        return _fail("no voice packs found")
    _ok(f"found {len(voices)} pack(s), {len(unlocked)} unlocked")
    for v in voices:
        status = "unlocked" if v["unlocked"] else "locked"
        print(f"     {v['id']}: {v['name']} [{status}] engine={v['engine']}")
        if v["engine"] == "piper" and v["unlocked"] and not (v.get("model") and os.path.exists(v["model"])):
            _fail(f"{v['id']} is unlocked but model file is missing")
            return False
    return True


def check_active_voice_speaks():
    print("Checking active voice TTS...")
    active = voice_toggle.load_voice()
    try:
        voice_toggle.speak(f"Health check. Active voice is {active}.")
        _ok(f"active voice '{active}' spoke")
        return True
    except Exception as exc:
        return _fail(f"active voice failed to speak: {exc}")


def check_voice_bridge():
    print("Checking voice-bridge service...")
    try:
        # FastAPI exposes GET /docs even when the main route only accepts POST.
        docs_url = VOICE_BRIDGE_URL.rsplit("/", 1)[0] + "/docs"
        urllib.request.urlopen(docs_url, timeout=2)
        _ok("voice-bridge is reachable")
        return True
    except Exception as exc:
        return _fail(f"voice-bridge not reachable at {VOICE_BRIDGE_URL}: {exc}")


def check_groq_key():
    print("Checking Groq API key...")
    env = load_env()
    key = os.environ.get("GROQ_API_KEY") or env.get("GROQ_API_KEY", "")
    if not key:
        return _fail("GROQ_API_KEY not set")
    _ok("GROQ_API_KEY is configured")
    return True


def check_stt():
    print("Checking STT pipeline...")
    active = voice_toggle.load_voice()
    voice = voice_toggle.get_voice(active)
    if not voice or not voice.get("model"):
        return _fail("cannot generate test audio: no active Piper model")
    model = voice["model"]
    fd, wav_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        text = "This is a health check for speech to text."
        proc = subprocess.run(
            ["piper", "--model", model, "--output_file", wav_path],
            input=text.encode(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30
        )
        if proc.returncode != 0:
            return _fail(f"piper test audio failed: {proc.stderr.decode()[:200]}")
        proc2 = subprocess.run(
            ["curl", "-s", "-X", "POST", VOICE_BRIDGE_URL,
             "-F", f"audio=@{wav_path}", "-F", "mode=transcribe_only",
             "-H", "Accept: application/json"],
            capture_output=True, text=True, timeout=30
        )
        try:
            result = json.loads(proc2.stdout)
        except Exception:
            return _fail(f"STT response not JSON: {proc2.stdout[:200]} / {proc2.stderr[:200]}")
        transcript = result.get("text", result.get("transcript", "")).strip()
        if not transcript:
            return _fail(f"STT returned empty transcript: {result}")
        _ok(f"STT transcribed test audio: '{transcript}'")
        return True
    except Exception as exc:
        return _fail(f"STT check failed: {exc}")
    finally:
        try:
            os.unlink(wav_path)
        except Exception:
            pass


def main():
    print("AI Controller Voice + STT Health Check\n")
    checks = [
        check_voices(),
        check_active_voice_speaks(),
        check_voice_bridge(),
        check_groq_key(),
        check_stt(),
    ]
    print()
    if all(checks):
        print("All checks passed. Voice and STT are ready.")
        sys.exit(0)
    else:
        print("Some checks failed. Fix the issues above and run again.")
        sys.exit(1)


if __name__ == "__main__":
    main()
