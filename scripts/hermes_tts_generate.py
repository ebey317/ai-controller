#!/usr/bin/env python3
"""Generate-only TTS bridge: Hermes speaks with whichever ai-controller voice
is currently active (the same one the controller's voice-toggle button sets),
instead of a voice hardcoded in Hermes's own config.yaml.

Contract (Hermes tts.providers.<name>, type: command):
    hermes_tts_generate.py --text <path to a text file> --output <path to write audio>

This script must ONLY generate audio at --output and exit 0/nonzero.
It must NEVER play audio itself -- Hermes plays the result after this
returns. (Wiring a playback-only script into this slot is exactly what
caused the month-long "TTS provider exited with code 1" outage; see
docs/hermes_tts_pipeline.md, Incident 001. Don't repeat that here.)
"""
import argparse
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
from voice_toggle import load_voice, get_voice  # reuse the tested voice resolution

PIPER_BIN = "/home/elijah/.local/bin/piper"
FFMPEG_BIN = "/usr/bin/ffmpeg"
EDGE_TTS_CANDIDATES = (
    os.path.expanduser("~/.hermes/hermes-agent/venv/bin/edge-tts"),
    os.path.expanduser("~/ai-controller/.venv/bin/edge-tts"),
)


def _resolve_edge_tts_bin():
    for cand in EDGE_TTS_CANDIDATES:
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    import shutil
    found = shutil.which("edge-tts")
    if found:
        return found
    raise RuntimeError("edge-tts binary not found in any known location")


def _piper_generate(text, model_path, output_path):
    if not model_path or not os.path.isfile(model_path):
        raise RuntimeError(f"piper model not found: {model_path!r}")
    proc = subprocess.run(
        [PIPER_BIN, "--model", model_path, "--output_file", output_path],
        input=text.encode("utf-8"),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"piper exited {proc.returncode}: {proc.stderr.decode(errors='replace')[:300]}")


def _edge_generate(text, voice, pitch, rate, output_path):
    edge_tts = _resolve_edge_tts_bin()
    tmp_mp3 = output_path + ".src.mp3"
    proc = subprocess.run(
        [edge_tts, "--voice", voice, f"--pitch={pitch}", f"--rate={rate}",
         "--text", text, "--write-media", tmp_mp3],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=25,
    )
    if proc.returncode != 0 or not os.path.exists(tmp_mp3) or os.path.getsize(tmp_mp3) == 0:
        if os.path.exists(tmp_mp3):
            os.remove(tmp_mp3)
        raise RuntimeError(f"edge-tts exited {proc.returncode}: {proc.stderr.decode(errors='replace')[:300]}")
    try:
        # Hermes's declared output_format decides the extension it expects at
        # output_path; convert only when that extension isn't already mp3.
        if output_path.lower().endswith(".mp3"):
            os.replace(tmp_mp3, output_path)
        else:
            conv = subprocess.run(
                [FFMPEG_BIN, "-y", "-loglevel", "error", "-i", tmp_mp3, output_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=20,
            )
            if conv.returncode != 0:
                raise RuntimeError(f"ffmpeg exited {conv.returncode}: {conv.stderr.decode(errors='replace')[:300]}")
    finally:
        if os.path.exists(tmp_mp3):
            os.remove(tmp_mp3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    with open(args.text, "r", encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        print("Error: input text file was empty", file=sys.stderr)
        sys.exit(1)

    voice_id = load_voice()
    voice = get_voice(voice_id)
    if voice is None:
        print(f"Error: could not resolve active voice (id={voice_id!r})", file=sys.stderr)
        sys.exit(1)

    out_dir = os.path.dirname(os.path.abspath(args.output))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    try:
        if voice.get("engine") == "piper":
            _piper_generate(text, voice.get("model"), args.output)
        else:
            _edge_generate(
                text,
                voice.get("voice", "en-US-AriaNeural"),
                voice.get("pitch", "+0Hz"),
                voice.get("rate", "+0%"),
                args.output,
            )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(args.output) or os.path.getsize(args.output) == 0:
        print(f"Error: no audio produced for voice {voice_id!r} (engine={voice.get('engine')!r})", file=sys.stderr)
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
