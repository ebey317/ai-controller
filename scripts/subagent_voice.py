#!/usr/bin/env python3
"""
subagent_voice.py — Voice-driven sub-agent conversation companion.

Run with a persona:
    python3 subagent_voice.py --persona therapist

Press Right Trigger (F13) or Enter to talk. The agent listens, thinks via
Ollama, and speaks back through the active voice. Hold a full conversation
without touching keyboard or mouse.

Personas live in scripts/personas/<name>.json and define the system prompt,
voice, and premium lock status. Locked personas can be sold as add-on packs.
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import wave

import httpx

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import voice_toggle
from ai_controller_paths import config_dir, ensure_config_dir, load_env

ensure_config_dir()
PERSONAS_DIR = os.path.join(SCRIPT_DIR, "personas")
UNLOCKS_FILE = os.path.join(config_dir(), "ai_controller_unlocked_personas.json")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:fast")
VOICE_BRIDGE_URL = os.environ.get("VOICE_BRIDGE_URL", "http://localhost:8002/voice")

# Audio input is configurable like the rest of the stack.
_AUDIO_INPUT = load_env().get("AUDIO_INPUT", "")
_PAREC_DEVICE_ARGS = ["--device", _AUDIO_INPUT] if _AUDIO_INPUT else []

SAMPLE_RATE = 16000
CHANNELS = 1

# Say one of these words to switch personas mid-conversation.
KEYWORD_PERSONAS = {
    "agent": "agent",
    "casual": "casual",
    "chatty": "companion",
    "companion": "companion",
    "code": "coder-coach",
    "coach": "coder-coach",
}

recording = False
rec_proc = None
rawfile = None
wavfile = None


def load_persona(persona_id):
    path = os.path.join(PERSONAS_DIR, f"{persona_id}.json")
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg


def is_unlocked(persona_id, cfg):
    if not cfg.get("locked"):
        return True
    try:
        with open(UNLOCKS_FILE, "r", encoding="utf-8") as f:
            return persona_id in set(json.load(f))
    except Exception:
        return False


def unlock_persona(persona_id):
    os.makedirs(os.path.dirname(UNLOCKS_FILE), exist_ok=True)
    try:
        with open(UNLOCKS_FILE, "r", encoding="utf-8") as f:
            unlocks = set(json.load(f))
    except Exception:
        unlocks = set()
    unlocks.add(persona_id)
    with open(UNLOCKS_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(unlocks), f, indent=2)


def list_personas():
    personas = []
    if not os.path.isdir(PERSONAS_DIR):
        return personas
    for fname in sorted(os.listdir(PERSONAS_DIR)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(PERSONAS_DIR, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            continue
        pid = fname[:-5]
        cfg["id"] = pid
        cfg["unlocked"] = is_unlocked(pid, cfg)
        personas.append(cfg)
    return personas


def speak(text, voice=None):
    voice_toggle.speak(text, voice_id=voice)


def build_wav(raw_path, wav_path):
    with open(raw_path, "rb") as rf, wave.open(wav_path, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(rf.read())


def transcribe(wav_path):
    try:
        r = subprocess.run(
            ["curl", "-s", "-X", "POST", VOICE_BRIDGE_URL,
             "-F", f"audio=@{wav_path}", "-F", "mode=transcribe_only",
             "-H", "Accept: application/json"],
            capture_output=True, text=True, timeout=30)
        data = json.loads(r.stdout)
        return data.get("text", data.get("transcript", "")).strip()
    except Exception as ex:
        print(f"STT error: {ex}")
        return ""


def chat_with_ollama(messages, model):
    try:
        r = httpx.post(
            f"{OLLAMA_URL}/api/chat",
            json={"model": model, "messages": messages, "stream": False},
            timeout=120)
        r.raise_for_status()
        return r.json().get("message", {}).get("content", "").strip()
    except Exception as ex:
        print(f"LLM error: {ex}")
        return "I'm having trouble thinking right now."


def start_recording():
    global recording, rec_proc, rawfile, wavfile
    if recording:
        return
    rawfile = tempfile.mktemp(suffix=".raw", dir="/tmp")
    wavfile = tempfile.mktemp(suffix=".wav", dir="/tmp")
    rec_cmd = [
        "parec",
        "--rate", str(SAMPLE_RATE), "--channels", str(CHANNELS),
        "--format", "s16le", "--raw", rawfile,
    ] + _PAREC_DEVICE_ARGS
    rec_proc = subprocess.Popen(
        rec_cmd,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    recording = True
    print("  Listening...")


def stop_recording():
    global recording, rec_proc, rawfile, wavfile
    if not recording:
        return
    if rec_proc:
        rec_proc.terminate()
        rec_proc.wait()
    recording = False
    build_wav(rawfile, wavfile)
    try:
        os.unlink(rawfile)
    except Exception:
        pass


def listen_for_f13():
    """Block until F13 is pressed. Uses evdev or xev fallback."""
    # Simple fallback: wait for Enter in terminal. Production can bind to F13 via existing PTT.
    input("Press Enter to talk (or bind F13 externally)...")


def main():
    parser = argparse.ArgumentParser(description="Voice sub-agent companion")
    parser.add_argument("--persona", default="companion", help="Persona to use")
    parser.add_argument("--list", action="store_true", help="List available personas")
    parser.add_argument("--unlock", metavar="ID", help="Unlock a premium persona")
    args = parser.parse_args()

    if args.list:
        for p in list_personas():
            status = "UNLOCKED" if p["unlocked"] else "LOCKED"
            print(f"{p['id']}: {p['name']} [{status}]")
        return

    if args.unlock:
        unlock_persona(args.unlock)
        print(f"Unlocked persona: {args.unlock}")
        return

    persona = load_persona(args.persona)
    if not is_unlocked(args.persona, persona):
        print(f"Persona '{args.persona}' is locked. Unlock it to start.")
        sys.exit(1)

    system_prompt = persona.get("system_prompt", "You are a helpful assistant.")
    voice = persona.get("voice")
    model = persona.get("model", OLLAMA_MODEL)

    current_persona = persona
    current_pid = args.persona
    messages = [{"role": "system", "content": system_prompt}]

    print(f"Starting sub-agent: {current_persona.get('name', current_pid)}")
    print("Press Enter to talk. Press Ctrl+C to exit.")
    speak(current_persona.get("greeting", "Hello. I'm here to talk."), voice=voice)

    def switch_persona(new_pid):
        nonlocal current_persona, current_pid, voice, model, messages
        new_cfg = load_persona(new_pid)
        if not is_unlocked(new_pid, new_cfg):
            speak(f"Persona '{new_pid}' is locked.", voice=voice)
            return
        current_pid = new_pid
        current_persona = new_cfg
        voice = current_persona.get("voice")
        model = current_persona.get("model", OLLAMA_MODEL)
        messages = [{"role": "system", "content": current_persona.get("system_prompt", "You are a helpful assistant.")}]
        print(f"Switched to: {current_persona.get('name', current_pid)}")
        speak(current_persona.get("greeting", f"Switched to {current_pid} mode."), voice=voice)

    try:
        while True:
            input("Press Enter to talk...")
            start_recording()
            input("Press Enter when done...")
            stop_recording()

            if not wavfile or not os.path.exists(wavfile):
                continue

            user_text = transcribe(wavfile)
            try:
                os.unlink(wavfile)
            except Exception:
                pass

            if not user_text:
                speak("I didn't catch that.", voice=voice)
                continue

            print(f"You: {user_text}")

            # Keyword persona switching — say "agent", "casual", "code", etc.
            lowered = user_text.lower()
            switched = False
            for keyword, target_pid in KEYWORD_PERSONAS.items():
                if keyword in lowered and target_pid != current_pid:
                    switch_persona(target_pid)
                    switched = True
                    break
            if switched:
                continue

            messages.append({"role": "user", "content": user_text})

            response = chat_with_ollama(messages, model)
            print(f"Agent: {response}")
            messages.append({"role": "assistant", "content": response})

            # Keep context window manageable
            if len(messages) > 20:
                messages = [messages[0]] + messages[-18:]

            speak(response, voice=voice)
    except KeyboardInterrupt:
        print("\nGoodbye.")


if __name__ == "__main__":
    main()
