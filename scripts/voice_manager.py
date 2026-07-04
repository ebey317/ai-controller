#!/usr/bin/env python3
"""
voice_manager.py — Manage AI Controller voice packs.

Commands:
    voice_manager.py --list                List installed voice packs
    voice_manager.py --install <zip>       Install a voice pack from a zip file
    voice_manager.py --unlock <voice_id>   Unlock a premium voice pack
    voice_manager.py --set <voice_id>      Set the active voice
    voice_manager.py --remove <voice_id>   Remove a voice pack

Voice packs are folders under voices/ with a config.json and (for free/unlocked
voices) a Piper .onnx model.
"""
import argparse
import json
import os
import shutil
import sys
import zipfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import voice_toggle
from ai_controller_paths import ai_controller_dir

VOICES_DIR = os.path.join(ai_controller_dir(), "voices")


def _pack_path(voice_id: str) -> str:
    return os.path.join(VOICES_DIR, voice_id)


def _load_config(voice_id: str) -> dict | None:
    path = os.path.join(_pack_path(voice_id), "config.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def cmd_list():
    if not os.path.isdir(VOICES_DIR):
        print("No voices directory found.")
        return
    voices = voice_toggle.list_voices()
    if not voices:
        print("No voice packs installed.")
        return
    active = voice_toggle.load_voice()
    print(f"{'ACTIVE':6} {'ID':12} {'NAME':12} {'STATUS':10} {'DESCRIPTION'}")
    for v in voices:
        is_active = "*" if v["id"] == active else ""
        status = "UNLOCKED" if v["unlocked"] else "LOCKED"
        print(f"{is_active:6} {v['id']:12} {v['name']:12} {status:10} {v.get('description', '')}")


def cmd_install(zip_path: str):
    if not os.path.isfile(zip_path):
        print(f"ERROR: file not found: {zip_path}")
        sys.exit(1)
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        # Find top-level folder name inside the zip.
        top = names[0].split("/")[0]
        if not top:
            print("ERROR: invalid zip structure")
            sys.exit(1)
        dest = _pack_path(top)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        zf.extractall(VOICES_DIR)

    cfg = _load_config(top)
    if cfg is None:
        print(f"ERROR: installed {top} but config.json is missing or invalid")
        sys.exit(1)

    # If the pack ships with a model, auto-unlock it.
    model = cfg.get("model", "")
    has_model = model and os.path.exists(os.path.join(dest, model))
    if has_model and cfg.get("locked"):
        voice_toggle.unlock(top)
        print(f"Installed and unlocked '{top}'.")
    elif has_model:
        print(f"Installed '{top}'.")
    else:
        print(f"Installed '{top}' (locked — drop the .onnx model and run --unlock).")


def cmd_unlock(voice_id: str):
    cfg = _load_config(voice_id)
    if cfg is None:
        print(f"ERROR: voice pack '{voice_id}' not found")
        sys.exit(1)
    model = cfg.get("model", "")
    if model and not os.path.exists(os.path.join(_pack_path(voice_id), model)):
        print(f"ERROR: model file missing for '{voice_id}' — add it before unlocking")
        sys.exit(1)
    voice_toggle.unlock(voice_id)
    print(f"Unlocked '{voice_id}'.")


def cmd_set(voice_id: str):
    voices = voice_toggle.list_voices()
    if not any(v["id"] == voice_id and v["unlocked"] for v in voices):
        print(f"ERROR: '{voice_id}' is not installed or is locked")
        sys.exit(1)
    voice_toggle.save_voice(voice_id)
    print(f"Active voice set to '{voice_id}'.")


def cmd_remove(voice_id: str):
    dest = _pack_path(voice_id)
    if not os.path.isdir(dest):
        print(f"ERROR: voice pack '{voice_id}' not found")
        sys.exit(1)
    shutil.rmtree(dest)
    print(f"Removed '{voice_id}'.")


def main():
    parser = argparse.ArgumentParser(description="Manage AI Controller voice packs")
    parser.add_argument("--list", action="store_true", help="List installed voice packs")
    parser.add_argument("--install", metavar="ZIP", help="Install a voice pack from zip")
    parser.add_argument("--unlock", metavar="VOICE", help="Unlock a premium voice pack")
    parser.add_argument("--set", metavar="VOICE", dest="set_voice", help="Set the active voice")
    parser.add_argument("--remove", metavar="VOICE", help="Remove a voice pack")
    args = parser.parse_args()

    if args.list:
        cmd_list()
    elif args.install:
        cmd_install(args.install)
    elif args.unlock:
        cmd_unlock(args.unlock)
    elif args.set_voice:
        cmd_set(args.set_voice)
    elif args.remove:
        cmd_remove(args.remove)
    else:
        cmd_list()


if __name__ == "__main__":
    main()
