#!/usr/bin/env python3
"""Shared voice profiles for AI Controller -- ONE file every speaker reads.

Lijah's rule (2026-10-31): the slide keyboard's TTS and the avatars
(Tahuti and the roster) must read ONE shared voice profile, so tuning a
voice in setup changes it everywhere at once. This module is that file's
reader/writer. It is stdlib-only so any tree (keyboard, bridge, setup,
doctor) can import it without pulling in numpy/torch.

File: ~/.config/ai-controller/voice-profiles.json
      (override with $AI_CONTROLLER_VOICE_PROFILES for tests)

Schema (version 1):
    {
      "version": 1,
      "keyboard": {"pack": "aria", "rate": 1.0, "pitch_semitones": 0.0},
      "avatars": {
        "tahuti": {"engine": "kokoro", "voice": "am_onyx",
                   "pitch_semitones": 0.0, "rate": 1.07},
        ...
      },
      "avatar": {"enabled_by_default": true, "scale": 1.0}
    }

- engine: "kokoro" | "piper" | "edge-tts"
- voice: engine voice id (kokoro: "am_onyx"; piper: pack id or model
  filename; edge-tts: "en-US-AriaNeural")
- rate: speaking-rate multiplier, 0.5..2.0 (1.0 = natural)
- pitch_semitones: -12..+12 (0 = natural). Kokoro previews apply it by
  resampling; Piper has no pitch control, so the value is stored and
  only takes effect on engines that support it.

Tahuti's defaults below are Lijah's locked pick of 2026-10-31:
Kokoro am_onyx, no pitch shift, rate 1.07 (his Osiris-cadence standard).
"""

import copy
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
from ai_controller_paths import config_dir  # noqa: E402

ENGINES = ("kokoro", "piper", "edge-tts")
RATE_MIN, RATE_MAX = 0.5, 2.0
PITCH_MIN, PITCH_MAX = -12.0, 12.0
SCALE_MIN, SCALE_MAX = 0.25, 3.0

DEFAULT_PROFILES = {
    "version": 1,
    # The keyboard's own speaking voice: a voice pack from voices/
    # (aria/joe today) plus shared rate/pitch tuning on top of the pack.
    "keyboard": {"pack": "aria", "rate": 1.0, "pitch_semitones": 0.0},
    "avatars": {
        # LOCKED by Lijah 2026-10-31 -- do not change these defaults
        # without his word.
        "tahuti": {
            "engine": "kokoro",
            "voice": "am_onyx",
            "pitch_semitones": 0.0,
            "rate": 1.07,
        },
    },
    # Slide-keyboard avatar overlay: present by default when the
    # keyboard is up (Lijah's standing decision, 2026-10-31).
    "avatar": {"enabled_by_default": True, "scale": 1.0},
}

PREVIEW_LINES = {
    "tahuti": (
        "I am Tahuti. I keep the record, I weigh the words, and "
        "nothing moves without your approval."
    ),
}
DEFAULT_PREVIEW_LINE = (
    "This is {name}, speaking at the pace and pitch you set for me."
)


def profiles_path() -> str:
    """Path of the shared voice-profiles file."""
    env = os.environ.get("AI_CONTROLLER_VOICE_PROFILES")
    if env:
        return os.path.expanduser(env)
    return os.path.join(config_dir(), "voice-profiles.json")


def _is_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def validate_profiles(data) -> list:
    """Return a list of human-readable problems (empty = valid)."""
    problems = []
    if not isinstance(data, dict):
        return ["top level is not an object"]
    if data.get("version") != 1:
        problems.append("version must be 1")

    kb = data.get("keyboard")
    if not isinstance(kb, dict):
        problems.append("keyboard section missing or not an object")
    else:
        if not isinstance(kb.get("pack"), str) or not kb.get("pack"):
            problems.append("keyboard.pack must be a non-empty string")
        _check_rate_pitch(kb, "keyboard", problems)

    avatars = data.get("avatars")
    if not isinstance(avatars, dict):
        problems.append("avatars section missing or not an object")
    else:
        for name, prof in avatars.items():
            where = f"avatars.{name}"
            if not isinstance(prof, dict):
                problems.append(f"{where} is not an object")
                continue
            if prof.get("engine") not in ENGINES:
                problems.append(
                    f"{where}.engine must be one of {', '.join(ENGINES)}"
                )
            if not isinstance(prof.get("voice"), str) or not prof.get("voice"):
                problems.append(f"{where}.voice must be a non-empty string")
            _check_rate_pitch(prof, where, problems)

    av = data.get("avatar")
    if not isinstance(av, dict):
        problems.append("avatar section missing or not an object")
    else:
        if not isinstance(av.get("enabled_by_default"), bool):
            problems.append("avatar.enabled_by_default must be true/false")
        scale = av.get("scale")
        if not _is_number(scale) or not (SCALE_MIN <= scale <= SCALE_MAX):
            problems.append(
                f"avatar.scale must be a number {SCALE_MIN}..{SCALE_MAX}"
            )
    return problems


def _check_rate_pitch(section, where, problems):
    rate = section.get("rate")
    if not _is_number(rate) or not (RATE_MIN <= rate <= RATE_MAX):
        problems.append(f"{where}.rate must be a number {RATE_MIN}..{RATE_MAX}")
    pitch = section.get("pitch_semitones")
    if not _is_number(pitch) or not (PITCH_MIN <= pitch <= PITCH_MAX):
        problems.append(
            f"{where}.pitch_semitones must be a number "
            f"{PITCH_MIN}..{PITCH_MAX}"
        )


def _deep_merge(base, override):
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_profiles(path=None) -> dict:
    """Load the shared profiles, merged over the locked defaults.

    A missing file yields the defaults (setup/doctor create the file).
    A present-but-invalid file raises ValueError listing the problems --
    callers that must never crash (e.g. the keyboard's speak path) catch
    it and fall back to defaults.
    """
    path = path or profiles_path()
    if not os.path.exists(path):
        return copy.deepcopy(DEFAULT_PROFILES)
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    merged = _deep_merge(DEFAULT_PROFILES, raw if isinstance(raw, dict) else {})
    problems = validate_profiles(merged)
    # Unknown top-level keys in the file are tolerated (forward-compat);
    # anything that breaks the known schema is not.
    problems = [p for p in problems if not p.startswith("version")]
    if raw.get("version") not in (None, 1):
        problems.append("version must be 1")
    if problems:
        raise ValueError("invalid voice profiles: " + "; ".join(problems))
    return merged


def save_profiles(data, path=None) -> str:
    """Validate and atomically write the profiles file. Returns the path."""
    problems = validate_profiles(data)
    if problems:
        raise ValueError("invalid voice profiles: " + "; ".join(problems))
    path = path or profiles_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return path


def get_avatar_profile(data, name) -> dict:
    """One avatar's profile, or a neutral piper default if unlisted."""
    prof = data.get("avatars", {}).get(name)
    if isinstance(prof, dict):
        return prof
    return {"engine": "piper", "voice": name,
            "pitch_semitones": 0.0, "rate": 1.0}


def preview_line(name) -> str:
    if name in PREVIEW_LINES:
        return PREVIEW_LINES[name]
    return DEFAULT_PREVIEW_LINE.format(name=name.capitalize())


if __name__ == "__main__":
    print(profiles_path())
    print(json.dumps(load_profiles(), indent=2))
