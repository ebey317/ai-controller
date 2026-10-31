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
      "avatar": {"enabled_by_default": true, "scale": 1.0,
                 "active_avatar": "tahuti",
                 "show_trigger": "talk_buttons", "size_px": 220,
                 "movement": "corner_loop",
                 "notification_corner": "top-right",
                 "respect_notification_corner": true,
                 "approval_box": true,
                 "show_on_talk": true, "stay_while_working": true,
                 "fade_when_done": true, "popup_on_approval": true}
    }

- engine: "kokoro" | "piper" | "edge-tts"
- voice: engine voice id (kokoro: "am_onyx"; piper: pack id or model
  filename; edge-tts: "en-US-AriaNeural")
- rate: speaking-rate multiplier, 0.5..2.0 (1.0 = natural)
- avatar.show_trigger: "talk_buttons" (the avatar pops up when the
  controller's L or R talk button is pressed -- the default) |
  "always_on" (present while the slide keyboard is up).
  avatar.enabled_by_default arms the avatar feature itself.
  avatar.size_px: small-companion on-screen size, 120..400 (220
  default). avatar.movement: "corner_loop" (top-left -> bottom-left
  -> bottom-right) | "stay_put"; avatar.notification_corner names
  the corner the loop never parks in while
  avatar.respect_notification_corner is true. avatar.approval_box:
  the avatar spawns its own small approval box beside itself, one
  at a time. Visibility lifecycle keys (all default true):
  show_on_talk (pop up while L/R is held), stay_while_working (stay
  visible with work animations after release), fade_when_done (fade
  to black once the result lands), popup_on_approval (pop up with
  the approval box whenever approval is needed). Older files
  without these keys merge over the defaults.
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

# ── avatar overlay (the "avatar" section) ────────────────────────────
# Presence is talk-driven (Lijah, 2026-10-31): pressing the
# controller's L or R talk button is what pops the avatar up and
# animates it. Always-on (present whenever the slide keyboard is up)
# is the alternate mode. "enabled_by_default" arms the feature;
# "show_trigger" picks how it appears.
SHOW_TRIGGERS = ("talk_buttons", "always_on")
# Visibility lifecycle under the talk-button trigger (Lijah,
# 2026-10-31): L/R held -> avatar pops up, talking/listening;
# L/R released, agent working -> avatar STAYS, work animations;
# work done -> the result lands, then the avatar fades to black;
# approval needed at any time -> avatar pops up with its box.
LIFECYCLE_KEYS = ("show_on_talk", "stay_while_working",
                  "fade_when_done", "popup_on_approval")
SHOW_TRIGGER_LABELS = {
    "talk_buttons": "on L/R talk-button press",
    "always_on": "always on while the keyboard is up",
}
# Small-companion size rule: the avatar stays a few percent of the
# screen (~180-260 px class). Bounded so a typo can't fill the screen.
AVATAR_SIZE_DEFAULT_PX = 220
AVATAR_SIZE_MIN_PX, AVATAR_SIZE_MAX_PX = 120, 400
# Corner loop: top-left -> bottom-left -> bottom-right. The
# notification corner is never parked in.
MOVEMENT_PATTERNS = ("corner_loop", "stay_put")
NOTIFICATION_CORNERS = ("top-right", "top-left", "bottom-right",
                        "bottom-left")

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
    # Slide-keyboard avatar overlay. Defaults locked by Lijah
    # 2026-10-31: feature armed, Tahuti active, pops up on the L/R
    # talk buttons, small-companion size, corner loop that never
    # parks in the notification corner, approval box beside itself.
    "avatar": {
        "enabled_by_default": True,
        "scale": 1.0,
        "active_avatar": "tahuti",
        "show_trigger": "talk_buttons",
        "size_px": AVATAR_SIZE_DEFAULT_PX,
        "movement": "corner_loop",
        "notification_corner": "top-right",
        "respect_notification_corner": True,
        "approval_box": True,
        "show_on_talk": True,
        "stay_while_working": True,
        "fade_when_done": True,
        "popup_on_approval": True,
    },
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
        # Extended avatar-setup keys: checked when present, so files
        # written before they existed still validate (load_profiles
        # merges the defaults in anyway).
        if "active_avatar" in av and (
            not isinstance(av.get("active_avatar"), str)
            or not av.get("active_avatar")
        ):
            problems.append(
                "avatar.active_avatar must be a non-empty string")
        if "show_trigger" in av and (
            av.get("show_trigger") not in SHOW_TRIGGERS
        ):
            problems.append(
                "avatar.show_trigger must be one of "
                + ", ".join(SHOW_TRIGGERS))
        if "size_px" in av:
            size = av.get("size_px")
            if not _is_number(size) or not (
                AVATAR_SIZE_MIN_PX <= size <= AVATAR_SIZE_MAX_PX
            ):
                problems.append(
                    f"avatar.size_px must be a number "
                    f"{AVATAR_SIZE_MIN_PX}..{AVATAR_SIZE_MAX_PX}")
        if "movement" in av and (
            av.get("movement") not in MOVEMENT_PATTERNS
        ):
            problems.append(
                "avatar.movement must be one of "
                + ", ".join(MOVEMENT_PATTERNS))
        if "notification_corner" in av and (
            av.get("notification_corner") not in NOTIFICATION_CORNERS
        ):
            problems.append(
                "avatar.notification_corner must be one of "
                + ", ".join(NOTIFICATION_CORNERS))
        for flag in ("respect_notification_corner", "approval_box",
                     "show_on_talk", "stay_while_working",
                     "fade_when_done", "popup_on_approval"):
            if flag in av and not isinstance(av.get(flag), bool):
                problems.append(f"avatar.{flag} must be true/false")
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


# ── avatar overlay helpers ───────────────────────────────────────────

def avatars_dir() -> str:
    """Where the avatar portraits live (assets/avatars in the repo)."""
    env = os.environ.get("AI_CONTROLLER_AVATARS_DIR")
    if env:
        return os.path.expanduser(env)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "assets", "avatars")


def available_avatars(directory=None) -> list:
    """Avatar names with a portrait on disk (tahuti, seba, ...)."""
    directory = directory or avatars_dir()
    names = []
    if os.path.isdir(directory):
        for fn in sorted(os.listdir(directory)):
            stem, ext = os.path.splitext(fn)
            if ext.lower() in (".webp", ".png", ".jpg", ".jpeg") and stem:
                names.append(stem)
    return names


def get_avatar_settings(data) -> dict:
    """The avatar overlay section, locked defaults merged in."""
    merged = copy.deepcopy(DEFAULT_PROFILES["avatar"])
    section = data.get("avatar") if isinstance(data, dict) else None
    if isinstance(section, dict):
        merged.update(section)
    return merged


def validate_avatar_settings(settings, directory=None) -> list:
    """Problems with an avatar settings dict (empty = good to show).

    Beyond the schema checks in validate_profiles: the active avatar
    must have a portrait on disk, or the overlay would show nothing.
    """
    problems = []
    active = settings.get("active_avatar")
    names = available_avatars(directory)
    if names and active not in names:
        problems.append(
            f"active avatar {active!r} has no portrait in "
            f"{directory or avatars_dir()} (found: {', '.join(names)})"
        )
    return problems


if __name__ == "__main__":
    print(profiles_path())
    print(json.dumps(load_profiles(), indent=2))
