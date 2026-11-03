#!/usr/bin/env python3
"""GTK3 Setup Center for the AI Controller.

A single scrollable page of plain-language setting cards. Each card shows
its current value, what it does, and a real control only when the setting
has a backed writer and consumer. Unbacked framework pieces are shown as
read-only "Not ready yet" with a concrete reason.

Sections: Controller, Voice, Tahuti, Sensei/Master AI, Services & Diagnostics.

Opened from the slide keyboard's SETUP button. The popup never accepts
focus and the launcher never calls a focus-raising method, so the target
window keeps focus.
"""

import json
import os
import shlex
import shutil
import subprocess
import sys
import textwrap
import threading
from typing import Optional

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import controller_setup as cs  # noqa: E402
import voice_profiles as vp  # noqa: E402
from ai_controller_paths import ai_controller_dir, config_dir  # noqa: E402

OK, WARN, FAIL = cs.OK, cs.WARN, cs.FAIL

HUD_ORANGE = "#FF6A00"

CSS = b"""
window { background-color: transparent; }
#setup-center {
    background-color: rgba(13,13,18,0.96);
    border: 2px solid #FF6A00;
    border-radius: 16px;
}
#setup-header {
    color: #FF6A00;
    font-family: monospace;
    font-size: 14px;
    font-weight: bold;
    margin-bottom: 6px;
}
.section-title {
    color: #e8e8e8;
    font-family: monospace;
    font-size: 13px;
    font-weight: bold;
    margin-top: 12px;
    margin-bottom: 6px;
}
.setting-card {
    background-color: rgba(26,26,32,0.70);
    border: 1px solid #2c2c36;
    border-radius: 8px;
    padding: 8px;
    margin-bottom: 6px;
}
.card-title {
    color: #FF6A00;
    font-family: monospace;
    font-size: 11px;
    font-weight: bold;
}
.card-desc {
    color: #a0a0a8;
    font-family: monospace;
    font-size: 10px;
    margin-top: 2px;
}
.card-current {
    color: #c8c8ce;
    font-family: monospace;
    font-size: 11px;
    margin-top: 4px;
}
.card-status {
    color: #8a8a92;
    font-family: monospace;
    font-size: 10px;
    margin-top: 4px;
}
.read-only {
    color: #6a6a72;
    font-family: monospace;
    font-size: 10px;
    font-style: italic;
    margin-top: 4px;
}
button {
    background-image: none;
    background-color: #23232b;
    color: #e8e8e8;
    border: 1px solid #3a3a44;
    border-radius: 6px;
    font-family: monospace;
    font-size: 11px;
    padding: 5px 10px;
    margin-left: 4px;
}
button:hover { background-color: #2f2f3a; }
button:disabled { color: #5a5a62; border-color: #2a2a32; }
button.action-primary { background-color: #FF6A00; color: #0d0d12; font-weight: bold; }
scrolledwindow { border: none; background: transparent; }
"""

PTT_MODES = ("pro", "bubbly", "casual", "bold", "big")
INPUT_TARGETS = ("type", "clipboard")
TAHUTI_ENGINES = ("kokoro", "piper", "edge")


def consumer_apply_status(key: str) -> str:
    """Return a plain-language status for whether ``apply_setting(key, ...)``
    has a live runtime consumer.

    This is a pure helper: it does not touch the filesystem, GTK, or any
    service.  The mapping is the contract the Setup Center cards use to
    decide between an editable control and a read-only "Not ready yet" label.
    """
    ready_next_use = {
        "ptt_mode",
        "input_target",
        "keyboard_pack",
        "keyboard_rate",
    }
    ready_next_approval = {
        "avatar_active",
        "avatar_size_px",
        "avatar_movement",
        "avatar_respect_corner",
        "avatar_notification_corner",
        "avatar_approval_box",
        "avatar_popup_approval",
    }
    not_connected_tahuti = {
        "tahuti_engine",
        "tahuti_voice",
        "tahuti_rate",
        "tahuti_pitch",
        "tahuti_gain",
        "avatar_show_on_talk",
        "avatar_stay_working",
        "avatar_fade_done",
    }
    if key in ready_next_use:
        return "Ready for your next use"
    if key in ready_next_approval:
        return "Ready the next time Tahuti asks for approval"
    if key in not_connected_tahuti:
        return "Not connected to Tahuti yet — its running voice loop uses tahuti_demo, not tahuti"
    return f"Not ready yet — no live consumer reads {key!r}"


def has_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _compute_setup_position(
    parent_geom: Optional[tuple[int, int, int, int]],
    setup_size: tuple[int, int],
    screen_size: tuple[int, int],
    gap: int = 8,
) -> tuple[int, int]:
    """Return (x, y) for the setup window.

    Centers horizontally over ``parent_geom`` and places it ``gap`` pixels
    above.  Clamps to ``screen_size`` so the window stays fully visible.
    Falls back to screen-center when ``parent_geom`` is None.

    Hermetic: no GTK calls, only arithmetic.
    """
    setup_w, setup_h = setup_size
    screen_w, screen_h = screen_size
    if parent_geom is not None:
        px, py, pw, ph = parent_geom
        x = px + (pw - setup_w) // 2
        y = py - setup_h - gap
        x = max(0, min(x, screen_w - setup_w))
        y = max(0, min(y, screen_h - setup_h))
        return x, y
    return (screen_w - setup_w) // 2, (screen_h - setup_h) // 2


def _ptt_mode_file() -> str:
    return os.path.join(config_dir(), "ptt_mode")


def _load_ptt_mode() -> str:
    try:
        with open(_ptt_mode_file(), "r", encoding="utf-8") as f:
            mode = f.read().strip().lower()
            if mode in PTT_MODES:
                return mode
    except (FileNotFoundError, OSError):
        pass
    return "pro"


def _save_ptt_mode(mode: str) -> str:
    os.makedirs(config_dir(), exist_ok=True)
    with open(_ptt_mode_file(), "w", encoding="utf-8") as f:
        f.write(mode)
    return mode


def _input_target_file() -> str:
    return os.path.join(config_dir(), "ai_controller_input_target")


def _load_input_target() -> str:
    try:
        with open(_input_target_file(), "r", encoding="utf-8") as f:
            target = f.read().strip().lower()
            if target in INPUT_TARGETS:
                return target
    except (FileNotFoundError, OSError):
        pass
    return "type"


def _save_input_target(target: str) -> str:
    os.makedirs(config_dir(), exist_ok=True)
    with open(_input_target_file(), "w", encoding="utf-8") as f:
        f.write(target)
    return target


def _load_voice_state_pack() -> Optional[str]:
    """Read the keyboard voice-pack state file that the runtime reads."""
    try:
        with open(os.path.join(config_dir(), "ai_controller_voice"), "r", encoding="utf-8") as f:
            return f.read().strip()
    except (FileNotFoundError, OSError):
        return None


def _discover_voice_packs() -> list:
    """List voice packs that have a config.json under voices/."""
    voices_dir = os.path.join(ai_controller_dir(), "voices")
    packs = []
    if os.path.isdir(voices_dir):
        for name in sorted(os.listdir(voices_dir)):
            if os.path.exists(os.path.join(voices_dir, name, "config.json")):
                packs.append(name)
    return packs


def _load_profiles() -> dict:
    try:
        return vp.load_profiles()
    except Exception:
        return vp.DEFAULT_PROFILES


def _save_profiles(data: dict) -> str:
    return vp.save_profiles(data)



def _verify_readback(key: str, desired) -> tuple:
    """Reload the authoritative source for ``key`` and compare with ``desired``.

    Returns ``(ok, actual, message)``. ``actual`` is ``None`` when the read
    itself fails.
    """
    try:
        if key == "ptt_mode":
            actual = _load_ptt_mode()
        elif key == "input_target":
            actual = _load_input_target()
        elif key == "keyboard_pack":
            data = vp.load_profiles()
            profile_pack = data.get("keyboard", {}).get("pack", "aria")
            state_pack = _load_voice_state_pack()
            if profile_pack != desired:
                actual = profile_pack
            else:
                actual = state_pack
            if profile_pack == desired and state_pack == desired:
                return True, desired, ""
        elif key == "keyboard_rate":
            actual = vp.load_profiles()["keyboard"]["rate"]
        elif key == "keyboard_pitch":
            actual = vp.load_profiles()["keyboard"]["pitch_semitones"]
        elif key == "keyboard_gain":
            actual = vp.load_profiles()["keyboard"]["gain"]
        elif key == "tahuti_engine":
            actual = vp.get_avatar_profile(vp.load_profiles(), "tahuti").get("engine", "kokoro")
        elif key == "tahuti_voice":
            actual = vp.get_avatar_profile(vp.load_profiles(), "tahuti").get("voice", "am_onyx")
        elif key == "tahuti_rate":
            actual = vp.get_avatar_profile(vp.load_profiles(), "tahuti").get("rate", 1.07)
        elif key == "tahuti_pitch":
            actual = vp.get_avatar_profile(vp.load_profiles(), "tahuti").get("pitch_semitones", 0.0)
        elif key == "tahuti_gain":
            actual = vp.get_avatar_profile(vp.load_profiles(), "tahuti").get("gain", 1.0)
        elif key == "avatar_active":
            actual = vp.get_avatar_settings(vp.load_profiles()).get("active_avatar", "tahuti")
        elif key == "avatar_show_trigger":
            actual = vp.get_avatar_settings(vp.load_profiles()).get("show_trigger", "talk_buttons")
        elif key == "avatar_enabled":
            actual = vp.get_avatar_settings(vp.load_profiles()).get("enabled_by_default", True)
        elif key == "avatar_size_px":
            actual = vp.get_avatar_settings(vp.load_profiles()).get("size_px", vp.AVATAR_SIZE_DEFAULT_PX)
        elif key == "avatar_movement":
            actual = vp.get_avatar_settings(vp.load_profiles()).get("movement", "corner_loop")
        elif key == "avatar_respect_corner":
            actual = vp.get_avatar_settings(vp.load_profiles()).get("respect_notification_corner", True)
        elif key == "avatar_notification_corner":
            actual = vp.get_avatar_settings(vp.load_profiles()).get("notification_corner", "top-right")
        elif key == "avatar_approval_box":
            actual = vp.get_avatar_settings(vp.load_profiles()).get("approval_box", True)
        elif key == "avatar_show_on_talk":
            actual = vp.get_avatar_settings(vp.load_profiles()).get("show_on_talk", True)
        elif key == "avatar_stay_working":
            actual = vp.get_avatar_settings(vp.load_profiles()).get("stay_while_working", True)
        elif key == "avatar_fade_done":
            actual = vp.get_avatar_settings(vp.load_profiles()).get("fade_when_done", True)
        elif key == "avatar_popup_approval":
            actual = vp.get_avatar_settings(vp.load_profiles()).get("popup_on_approval", True)
        else:
            return False, None, f"no readback source for {key!r}"
    except Exception as exc:
        return False, None, f"Saved but could not read back: {exc}"

    if actual != desired:
        return False, actual, f"Wrote {desired!r} but source reads {actual!r}"
    return True, actual, ""


def _verified(key: str, desired, base_message: str) -> tuple:
    """Return a verified success tuple, or a mismatch/read-failure tuple."""
    ok, actual, msg = _verify_readback(key, desired)
    if not ok:
        return False, actual, msg
    return True, actual, f"{base_message} — {consumer_apply_status(key)}"


def _keyboard_voice_profile(data: dict) -> dict:
    """Build a speakable profile dict from the keyboard pack + tuning.

    Reads the actual voices/<pack>/config.json to determine engine and the
    right voice identifier, instead of assuming every pack is Piper.
    """
    kb = data.get("keyboard", {})
    pack = kb.get("pack", "aria")
    pack_cfg = _load_voice_pack_config(pack)
    engine = pack_cfg.get("engine")
    if not engine:
        raise VoicePackConfigError(
            f"voice pack {pack!r} config does not declare an engine"
        )
    if vp.canonical_engine(engine) == "edge":
        voice = pack_cfg.get("voice")
        if not voice:
            raise VoicePackConfigError(
                f"voice pack {pack!r} edge config does not declare a voice"
            )
    elif engine == "piper":
        # Piper model resolution needs the pack id so _piper_model_for can
        # find voices/<pack>/config.json and the model file it names.
        voice = pack
    else:
        voice = pack_cfg.get("voice", pack)
    knobs = vp.knob_values(kb)
    return {
        "engine": engine,
        "voice": voice,
        "rate": float(kb.get("rate", 1.0)),
        "pitch_semitones": float(kb.get("pitch_semitones", 0.0)),
        "gain": knobs["gain"],
    }


class VoicePackConfigError(Exception):
    """Raised when a voice-pack config is missing or unusable."""


def _load_voice_pack_config(pack_id: str) -> dict:
    """Load voices/<pack_id>/config.json and return its contents.

    Raises VoicePackConfigError with a plain reason if the file is missing
    or cannot be parsed, so the UI can surface an honest disabled message.
    """
    cfg_path = os.path.join(ai_controller_dir(), "voices", pack_id, "config.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise VoicePackConfigError(
            f"voice pack {pack_id!r} has no config.json at {cfg_path}"
        ) from None
    except json.JSONDecodeError as exc:
        raise VoicePackConfigError(
            f"voice pack {pack_id!r} config is invalid JSON: {exc}"
        ) from exc
    except OSError as exc:
        raise VoicePackConfigError(
            f"voice pack {pack_id!r} config could not be read: {exc}"
        ) from exc


def _run_keyboard_voice_test() -> str:
    """Run a keyboard voice test, surfacing pack-config errors honestly."""
    try:
        profile = _keyboard_voice_profile(_load_profiles())
    except VoicePackConfigError as exc:
        return f"Voice test disabled: {exc}"
    return run_voice_test(profile, "keyboard")


def _tahuti_voice_profile(data: dict) -> dict:
    prof = vp.get_avatar_profile(data, "tahuti")
    knobs = vp.knob_values(prof)
    return {
        "engine": prof.get("engine", "kokoro"),
        "voice": prof.get("voice", "am_onyx"),
        "rate": float(prof.get("rate", 1.0)),
        "pitch_semitones": float(prof.get("pitch_semitones", 0.0)),
        "gain": knobs["gain"],
    }


def current_settings() -> dict:
    """Return current values for every setting shown in the Setup Center."""
    data = _load_profiles()
    kb = data.get("keyboard", {})
    tahuti = vp.get_avatar_profile(data, "tahuti")
    avatar = vp.get_avatar_settings(data)
    return {
        "ptt_mode": _load_ptt_mode(),
        "input_target": _load_input_target(),
        "voice_packs": _discover_voice_packs(),
        "keyboard_pack": kb.get("pack", "aria"),
        "keyboard_rate": float(kb.get("rate", 1.0)),
        "keyboard_pitch": float(kb.get("pitch_semitones", 0.0)),
        "keyboard_gain": vp.knob_values(kb)["gain"],
        "tahuti_engine": tahuti.get("engine", "kokoro"),
        "tahuti_voice": tahuti.get("voice", "am_onyx"),
        "tahuti_rate": float(tahuti.get("rate", 1.0)),
        "tahuti_pitch": float(tahuti.get("pitch_semitones", 0.0)),
        "tahuti_gain": vp.knob_values(tahuti)["gain"],
        "avatar_active": avatar.get("active_avatar", "tahuti"),
        "avatar_show_trigger": avatar.get("show_trigger", "talk_buttons"),
        "avatar_enabled": bool(avatar.get("enabled_by_default", True)),
        "avatar_size_px": int(avatar.get("size_px", vp.AVATAR_SIZE_DEFAULT_PX)),
        "avatar_movement": avatar.get("movement", "corner_loop"),
        "avatar_respect_corner": bool(avatar.get("respect_notification_corner", True)),
        "avatar_notification_corner": avatar.get("notification_corner", "top-right"),
        "avatar_approval_box": bool(avatar.get("approval_box", True)),
        "avatar_show_on_talk": bool(avatar.get("show_on_talk", True)),
        "avatar_stay_working": bool(avatar.get("stay_while_working", True)),
        "avatar_fade_done": bool(avatar.get("fade_when_done", True)),
        "avatar_popup_approval": bool(avatar.get("popup_on_approval", True)),
    }


def apply_setting(key: str, value) -> tuple:
    """Apply one setting. Returns (ok: bool, readback_value, message: str)."""
    data = _load_profiles()
    kb = data.setdefault("keyboard", {})
    avatars = data.setdefault("avatars", {})
    tahuti = avatars.setdefault("tahuti", {})
    avatar = data.setdefault("avatar", {})

    try:
        if key == "ptt_mode":
            if value not in PTT_MODES:
                return False, _load_ptt_mode(), f"unknown PTT mode {value!r}"
            readback = _save_ptt_mode(value)
            return _verified("ptt_mode", readback, f"PTT style set to {readback}")

        if key == "input_target":
            if value not in INPUT_TARGETS:
                return False, _load_input_target(), f"unknown input target {value!r}"
            readback = _save_input_target(value)
            return _verified("input_target", readback, f"Input target set to {readback}")

        if key == "keyboard_pack":
            packs = _discover_voice_packs()
            current_pack = kb.get("pack", "aria")
            if packs:
                if value not in packs:
                    return False, current_pack, f"voice pack {value!r} not found"
            else:
                if value != current_pack:
                    return False, current_pack, f"no voice packs installed; only {current_pack!r} is permitted"
            kb["pack"] = value
            _save_profiles(data)
            state_file = os.path.join(config_dir(), "ai_controller_voice")
            try:
                with open(state_file, "w", encoding="utf-8") as f:
                    f.write(value)
            except Exception as exc:
                return False, kb["pack"], f"Profiles saved but voice state file could not be written: {exc}"
            return _verified("keyboard_pack", value, f"Keyboard voice pack set to {value}")

        if key == "keyboard_rate":
            kb["rate"] = float(value)
            _save_profiles(data)
            return _verified("keyboard_rate", kb["rate"], f"Keyboard speed set to {kb['rate']}")

        if key == "keyboard_pitch":
            kb["pitch_semitones"] = float(value)
            _save_profiles(data)
            return _verified("keyboard_pitch", kb["pitch_semitones"], f"Keyboard pitch set to {kb['pitch_semitones']}st")

        if key == "keyboard_gain":
            kb["gain"] = float(value)
            _save_profiles(data)
            return _verified("keyboard_gain", kb["gain"], f"Keyboard volume set to {kb['gain']}")

        if key == "tahuti_engine":
            if value not in TAHUTI_ENGINES:
                return False, tahuti.get("engine", "kokoro"), f"unknown engine {value!r}"
            tahuti["engine"] = value
            _save_profiles(data)
            return _verified("tahuti_engine", value, f"Tahuti engine set to {value}")

        if key == "tahuti_voice":
            tahuti["voice"] = str(value)
            _save_profiles(data)
            return _verified("tahuti_voice", str(value), f"Tahuti voice id set to {value}")

        if key == "tahuti_rate":
            tahuti["rate"] = float(value)
            _save_profiles(data)
            return _verified("tahuti_rate", tahuti["rate"], f"Tahuti speed set to {tahuti['rate']}")

        if key == "tahuti_pitch":
            tahuti["pitch_semitones"] = float(value)
            _save_profiles(data)
            return _verified("tahuti_pitch", tahuti["pitch_semitones"], f"Tahuti pitch set to {tahuti['pitch_semitones']}st")

        if key == "tahuti_gain":
            tahuti["gain"] = float(value)
            _save_profiles(data)
            return _verified("tahuti_gain", tahuti["gain"], f"Tahuti volume set to {tahuti['gain']}")

        if key == "avatar_active":
            names = vp.available_avatars()
            current_avatar = avatar.get("active_avatar", "tahuti")
            if names:
                if value not in names:
                    return False, current_avatar, f"no portrait for {value!r}"
            else:
                if value != current_avatar:
                    return False, current_avatar, f"no portraits installed; only {current_avatar!r} is permitted"
            avatar["active_avatar"] = value
            _save_profiles(data)
            return _verified("avatar_active", value, f"Active avatar set to {value}")

        if key == "avatar_show_trigger":
            if value not in vp.SHOW_TRIGGERS:
                return False, avatar.get("show_trigger", "talk_buttons"), f"unknown trigger {value!r}"
            avatar["show_trigger"] = value
            _save_profiles(data)
            return _verified("avatar_show_trigger", value, f"Avatar shows {vp.SHOW_TRIGGER_LABELS[value]}")

        if key == "avatar_enabled":
            desired = bool(value)
            avatar["enabled_by_default"] = desired
            _save_profiles(data)
            return _verified("avatar_enabled", desired, "Avatar feature " + ("armed" if desired else "disarmed"))

        if key == "avatar_size_px":
            size = int(value)
            if not (vp.AVATAR_SIZE_MIN_PX <= size <= vp.AVATAR_SIZE_MAX_PX):
                return False, avatar.get("size_px", vp.AVATAR_SIZE_DEFAULT_PX), "size out of range"
            avatar["size_px"] = size
            _save_profiles(data)
            return _verified("avatar_size_px", size, f"Avatar size set to {size}px")

        if key == "avatar_movement":
            if value not in vp.MOVEMENT_PATTERNS:
                return False, avatar.get("movement", "corner_loop"), f"unknown movement {value!r}"
            avatar["movement"] = value
            _save_profiles(data)
            return _verified("avatar_movement", value, f"Avatar movement set to {value}")

        if key == "avatar_respect_corner":
            desired = bool(value)
            avatar["respect_notification_corner"] = desired
            _save_profiles(data)
            return _verified("avatar_respect_corner", desired, "Notification-corner respect updated")

        if key == "avatar_notification_corner":
            if value not in vp.NOTIFICATION_CORNERS:
                return False, avatar.get("notification_corner", "top-right"), f"unknown corner {value!r}"
            avatar["notification_corner"] = value
            _save_profiles(data)
            return _verified("avatar_notification_corner", value, f"Notification corner set to {value}")

        if key == "avatar_approval_box":
            desired = bool(value)
            avatar["approval_box"] = desired
            _save_profiles(data)
            return _verified("avatar_approval_box", desired, "Approval box updated")

        if key == "avatar_show_on_talk":
            desired = bool(value)
            avatar["show_on_talk"] = desired
            _save_profiles(data)
            return _verified("avatar_show_on_talk", desired, "Talk pop-up updated")

        if key == "avatar_stay_working":
            desired = bool(value)
            avatar["stay_while_working"] = desired
            _save_profiles(data)
            return _verified("avatar_stay_working", desired, "Stay-while-working updated")

        if key == "avatar_fade_done":
            desired = bool(value)
            avatar["fade_when_done"] = desired
            _save_profiles(data)
            return _verified("avatar_fade_done", desired, "Fade-when-done updated")

        if key == "avatar_popup_approval":
            desired = bool(value)
            avatar["popup_on_approval"] = desired
            _save_profiles(data)
            return _verified("avatar_popup_approval", desired, "Approval pop-up updated")

        return False, None, f"unknown setting {key!r}"
    except Exception as exc:
        return False, None, f"Save failed: {exc}"


def run_voice_test(profile: dict, name: str = "voice", text: Optional[str] = None) -> str:
    """Speak a preview line for a profile and return an honest outcome string.

    Uses controller_setup.speak_preview, which itself returns False when the
    engine binary or model is missing. Never starts a service.
    """
    if not has_display():
        return "Test voice needs a display."
    ok = cs.speak_preview(profile, name, text=text)
    return "Voice test played." if ok else "Voice test could not play (engine/model missing or no network)."


def gather_health():
    """Collect doctor rows from the controller_setup registry, grouped by
    category in the order defined by ``cs.CATEGORIES``.
    """
    by_cat = {}
    for backend in cs.BACKENDS:
        source, category_key, result = cs.backend_status(backend.name)
        if category_key is None:
            continue
        section = by_cat.setdefault(
            category_key,
            {
                "category_key": category_key,
                "category_label": cs.CATEGORIES.get(category_key, category_key),
                "rows": [],
            },
        )
        rows = result if isinstance(result, list) else [result]
        for level, label, detail in rows:
            section["rows"].append(
                {
                    "backend": backend.name,
                    "source": source,
                    "level": level,
                    "label": label,
                    "detail": detail,
                }
            )
    return [by_cat[key] for key in cs.CATEGORIES if key in by_cat]


def redact_detail(detail: str, home_path: str = None) -> str:
    if home_path is None:
        home_path = os.path.expanduser("~")
    if home_path and home_path != "/":
        return detail.replace(home_path, "~")
    return detail


def redacted_doctor_text(home_path: str = None) -> str:
    rows = cs.run_doctor()
    lines = [
        "AI Controller -- diagnostics / support output",
        "Secret values are never shown in this output.",
        "",
    ]
    for level, label, detail in rows:
        lines.append(
            f"{level:<4}  {label}: {redact_detail(detail, home_path)}"
        )
    return "\n".join(lines)


def open_voice_board() -> str:
    return cs.launch_voice_board()


def open_wizard_in_terminal() -> str:
    if not has_display():
        return "The setup wizard needs a display."
    script = os.path.join(SCRIPT_DIR, "controller_setup.py")
    inner = (
        f"cd {shlex.quote(os.path.dirname(script))} && "
        f"{shlex.quote(sys.executable)} {shlex.quote(script)}; exec bash"
    )
    for term_cmd in (
        ["gnome-terminal", "--", "bash", "-lc", inner],
        ["x-terminal-emulator", "-e", "bash", "-lc", inner],
        ["xterm", "-e", "bash", "-lc", inner],
    ):
        if shutil.which(term_cmd[0]):
            try:
                subprocess.Popen(term_cmd)
            except Exception as exc:
                return f"Could not launch terminal: {exc}"
            return "Setup wizard opened in a terminal."
    return "No supported terminal emulator found."


class _Card:
    """One plain-language setting card."""

    def __init__(self, section_box, title: str, description: str, current_value):
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.box.set_name("setting-card")
        self.box.get_style_context().add_class("setting-card")
        section_box.pack_start(self.box, False, False, 0)

        title_lbl = Gtk.Label(label=title)
        title_lbl.get_style_context().add_class("card-title")
        title_lbl.set_halign(Gtk.Align.START)
        self.box.pack_start(title_lbl, False, False, 0)

        desc_lbl = Gtk.Label(label=description)
        desc_lbl.get_style_context().add_class("card-desc")
        desc_lbl.set_halign(Gtk.Align.START)
        desc_lbl.set_line_wrap(True)
        self.box.pack_start(desc_lbl, False, False, 0)

        self.value_lbl = Gtk.Label(label=f"Current: {current_value}")
        self.value_lbl.get_style_context().add_class("card-current")
        self.value_lbl.set_halign(Gtk.Align.START)
        self.box.pack_start(self.value_lbl, False, False, 0)

        self.controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.controls.set_margin_top(4)
        self.box.pack_start(self.controls, False, False, 0)

        self.status_lbl = Gtk.Label(label="")
        self.status_lbl.get_style_context().add_class("card-status")
        self.status_lbl.set_halign(Gtk.Align.START)
        self.status_lbl.set_line_wrap(True)
        self.box.pack_start(self.status_lbl, False, False, 0)

    def set_status(self, text: str):
        GLib.idle_add(self.status_lbl.set_text, text)

    def set_current(self, text: str):
        GLib.idle_add(self.value_lbl.set_text, f"Current: {text}")

    def add_editable(self, control, apply_fn, test_fn=None):
        self.controls.pack_start(control, False, False, 0)

        apply_btn = Gtk.Button(label="Apply")
        apply_btn.get_style_context().add_class("action-primary")
        apply_btn.set_sensitive(False)
        self.controls.pack_start(apply_btn, False, False, 0)

        test_btn = None
        if test_fn is not None:
            test_btn = Gtk.Button(label="Test")
            self.controls.pack_start(test_btn, False, False, 0)
            test_btn.connect("clicked", lambda _b: self.set_status(test_fn()))

        def do_apply(_b):
            value = _read_control(control)
            ok, readback, msg = apply_fn(value)
            if ok:
                self.set_current(str(readback))
                apply_btn.set_sensitive(False)
            self.set_status(msg)

        apply_btn.connect("clicked", do_apply)

        def on_changed(_w):
            apply_btn.set_sensitive(True)

        if isinstance(control, Gtk.ComboBoxText):
            control.connect("changed", on_changed)
        elif isinstance(control, Gtk.SpinButton):
            control.connect("value-changed", on_changed)
        elif isinstance(control, Gtk.Entry):
            control.connect("changed", on_changed)
        elif isinstance(control, Gtk.Switch):
            control.connect("notify::active", on_changed)

    def add_readonly(self, reason: str):
        lbl = Gtk.Label(label=f"Not ready yet — {reason}")
        lbl.get_style_context().add_class("read-only")
        lbl.set_halign(Gtk.Align.START)
        lbl.set_line_wrap(True)
        self.controls.pack_start(lbl, False, False, 0)


def _read_control(control):
    if isinstance(control, Gtk.ComboBoxText):
        return control.get_active_text()
    if isinstance(control, Gtk.SpinButton):
        return control.get_value()
    if isinstance(control, Gtk.Entry):
        return control.get_text()
    if isinstance(control, Gtk.Switch):
        return control.get_active()
    return None


def _choice_control(choices, current):
    cb = Gtk.ComboBoxText()
    active = 0
    for i, val in enumerate(choices):
        cb.append_text(val)
        if val == current:
            active = i
    cb.set_active(active)
    return cb


def _spin_control(value: float, lo: float, hi: float, step: float, digits: int = 2):
    adj = Gtk.Adjustment(value=value, lower=lo, upper=hi,
                         step_increment=step, page_increment=step * 10)
    spin = Gtk.SpinButton(adjustment=adj, climb_rate=0.1, digits=digits)
    spin.set_numeric(True)
    return spin


class SetupCenter(Gtk.Window):
    """Scrollable setup control center."""

    WIDTH = 640
    HEIGHT = 520

    def __init__(self, parent=None):
        # TOPLEVEL is needed for the window manager to honor keep-above.
        # Focus stays disabled, so mapping this page cannot steal the typing
        # target from the themed slide keyboard or any other application.
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        # Setup is an interactive control center: it must accept focus so
        # controller/keyboard users can choose controls, drag the header, and
        # activate Close. The slide keyboard itself remains focus-disabled.
        self.set_accept_focus(True)
        self.set_default_size(self.WIDTH, self.HEIGHT)
        self.set_app_paintable(True)
        self.parent_window = parent

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)

        css = Gtk.CssProvider()
        css.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            screen, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.panel.set_name("setup-center")
        self.panel.set_margin_start(10)
        self.panel.set_margin_end(10)
        self.panel.set_margin_top(10)
        self.panel.set_margin_bottom(10)
        self.add(self.panel)

        header_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header_bar.set_halign(Gtk.Align.FILL)

        drag_handle = Gtk.EventBox()
        drag_handle.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        drag_handle.connect("button-press-event", self._on_drag_begin)
        header = Gtk.Label(label="AI Controller — Setup Center  •  drag here")
        header.set_name("setup-header")
        header.set_halign(Gtk.Align.START)
        drag_handle.add(header)
        header_bar.pack_start(drag_handle, True, True, 0)

        close_btn = Gtk.Button(label="Close")
        close_btn.set_tooltip_text("Close Setup Center")
        close_btn.connect("clicked", self._on_close_button)
        header_bar.pack_end(close_btn, False, False, 0)
        self.panel.pack_start(header_bar, False, False, 0)

        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self._scrolled.set_vexpand(True)
        self.panel.pack_start(self._scrolled, True, True, 0)

        self.cards_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.cards_box.set_margin_start(6)
        self.cards_box.set_margin_end(6)
        self._scrolled.add(self.cards_box)

        self.status_label = Gtk.Label(label="")
        self.status_label.get_style_context().add_class("card-status")
        self.status_label.set_halign(Gtk.Align.START)
        self.status_label.set_line_wrap(True)
        self.panel.pack_start(self.status_label, False, False, 0)

        self._build_sections()

        self.connect("delete-event", self._on_close)

    def _set_status(self, text: str):
        GLib.idle_add(self.status_label.set_text, text)

    def _section_box(self, title: str):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_margin_top(8)
        lbl = Gtk.Label(label=title)
        lbl.get_style_context().add_class("section-title")
        lbl.set_halign(Gtk.Align.START)
        box.pack_start(lbl, False, False, 0)
        self.cards_box.pack_start(box, False, False, 0)
        return box

    def _gather_data(self):
        """Collect all non-GTK data needed to rebuild the panel."""
        return {
            "settings": current_settings(),
            "health": gather_health(),
            "sensei": cs.backend_status("sensei-bridge"),
        }

    def _rebuild_sections(self, settings, health, sensei):
        """Rebuild every section using precomputed data.

        Preserves the previous vertical scroll position when practical.
        """
        vadj = self._scrolled.get_vadjustment()
        scroll_value = vadj.get_value() if vadj else 0

        for child in self.cards_box.get_children():
            self.cards_box.remove(child)
        self._settings = settings
        self._health = health
        self._sensei_status = sensei
        self._build_controller_section()
        self._build_voice_section()
        self._build_tahuti_section()
        self._build_sensei_section()
        self._build_services_section()
        self.cards_box.show_all()
        self._health_rows_box.show_all()

        if vadj:
            upper = vadj.get_upper() - vadj.get_page_size()
            if upper > 0:
                vadj.set_value(min(scroll_value, upper))

    def _build_sections(self):
        data = self._gather_data()
        self._rebuild_sections(data["settings"], data["health"], data["sensei"])

    def _build_controller_section(self):
        box = self._section_box("Controller")

        ptt = _Card(box, "PTT text style",
                    "How dictated text looks when sent from the controller.",
                    _load_ptt_mode())
        ptt.add_editable(
            _choice_control(PTT_MODES, _load_ptt_mode()),
            lambda v: apply_setting("ptt_mode", v),
        )

        target = _Card(box, "Input target",
                       "Type straight into the focused window, or copy to the clipboard only.",
                       _load_input_target())
        target.add_editable(
            _choice_control(INPUT_TARGETS, _load_input_target()),
            lambda v: apply_setting("input_target", v),
        )

    def _build_voice_section(self):
        box = self._section_box("Voice")
        s = self._settings

        packs = s.get("voice_packs") or ["aria"]
        pack_card = _Card(box, "Keyboard voice pack",
                          "The voice pack the keyboard speaks with.",
                          s["keyboard_pack"])
        pack_card.add_editable(
            _choice_control(packs, s["keyboard_pack"]),
            lambda v: apply_setting("keyboard_pack", v),
            lambda: _run_keyboard_voice_test(),
        )

        rate_card = _Card(box, "Keyboard speaking speed",
                          "Multiplier: 0.5 slow, 1.0 natural, 2.0 fast.",
                          s["keyboard_rate"])
        rate_card.add_editable(
            _spin_control(s["keyboard_rate"], vp.RATE_MIN, vp.RATE_MAX, 0.05),
            lambda v: apply_setting("keyboard_rate", v),
            lambda: _run_keyboard_voice_test(),
        )

        pitch_card = _Card(box, "Keyboard pitch",
                           "Pitch shift in semitones (-12 .. +12).",
                           s["keyboard_pitch"])
        pitch_card.add_readonly(
            "voice_toggle.speak/_piper_speak does not apply keyboard pitch yet"
        )

        gain_card = _Card(box, "Keyboard volume / gain",
                          "Output volume multiplier: 0 mute, 1 natural, 2 double.",
                          s["keyboard_gain"])
        gain_card.add_readonly(
            "voice_toggle.speak/_piper_speak does not apply keyboard gain yet"
        )

        engine_card = _Card(box, "Tahuti voice engine",
                            "Speech engine Tahuti uses for replies.",
                            s["tahuti_engine"])
        engine_card.add_editable(
            _choice_control(list(TAHUTI_ENGINES), s["tahuti_engine"]),
            lambda v: apply_setting("tahuti_engine", v),
        )

        voice_card = _Card(box, "Tahuti voice id",
                           "Voice identifier for the chosen engine (e.g. am_onyx for Kokoro).",
                           s["tahuti_voice"])
        voice_card.add_editable(
            Gtk.Entry(text=s["tahuti_voice"]),
            lambda v: apply_setting("tahuti_voice", v),
            lambda: run_voice_test(_tahuti_voice_profile(_load_profiles()), "tahuti"),
        )

        trate_card = _Card(box, "Tahuti speaking speed",
                           "Multiplier: 0.5 slow, 1.0 natural, 2.0 fast.",
                           s["tahuti_rate"])
        trate_card.add_editable(
            _spin_control(s["tahuti_rate"], vp.RATE_MIN, vp.RATE_MAX, 0.05),
            lambda v: apply_setting("tahuti_rate", v),
            lambda: run_voice_test(_tahuti_voice_profile(_load_profiles()), "tahuti"),
        )

        tpitch_card = _Card(box, "Tahuti pitch",
                            "Pitch shift in semitones (-12 .. +12).",
                            s["tahuti_pitch"])
        tpitch_card.add_editable(
            _spin_control(s["tahuti_pitch"], vp.PITCH_MIN, vp.PITCH_MAX, 0.5),
            lambda v: apply_setting("tahuti_pitch", v),
            lambda: run_voice_test(_tahuti_voice_profile(_load_profiles()), "tahuti"),
        )

        tgain_card = _Card(box, "Tahuti volume / gain",
                           "Output volume multiplier: 0 mute, 1 natural, 2 double.",
                           s["tahuti_gain"])
        tgain_card.add_editable(
            _spin_control(s["tahuti_gain"], vp.GAIN_MIN, vp.GAIN_MAX, 0.05),
            lambda v: apply_setting("tahuti_gain", v),
            lambda: run_voice_test(_tahuti_voice_profile(_load_profiles()), "tahuti"),
        )

    def _build_tahuti_section(self):
        box = self._section_box("Tahuti")
        s = self._settings

        avatars = vp.available_avatars() or ["tahuti"]
        active_card = _Card(box, "Active avatar",
                            "Which portrait the Tahuti overlay shows.",
                            s["avatar_active"])
        active_card.add_editable(
            _choice_control(avatars, s["avatar_active"]),
            lambda v: apply_setting("avatar_active", v),
        )

        trigger_card = _Card(box, "Show trigger",
                             "When the avatar pops up.",
                             vp.SHOW_TRIGGER_LABELS.get(s["avatar_show_trigger"], s["avatar_show_trigger"]))
        trigger_card.add_readonly(
            "no live runtime consumer reads avatar.show_trigger yet"
        )

        armed_card = _Card(box, "Avatar armed",
                           "Turn the avatar overlay feature on or off.",
                           "on" if s["avatar_enabled"] else "off")
        armed_card.add_readonly(
            "no live runtime consumer reads avatar.enabled_by_default yet"
        )

        size_card = _Card(box, "Avatar size",
                          "On-screen size in pixels (small companion).",
                          f"{s['avatar_size_px']}px")
        size_card.add_editable(
            _spin_control(s["avatar_size_px"], vp.AVATAR_SIZE_MIN_PX,
                        vp.AVATAR_SIZE_MAX_PX, 5, digits=0),
            lambda v: apply_setting("avatar_size_px", v),
        )

        move_card = _Card(box, "Avatar movement",
                          "Corner loop animation or stay put.",
                          s["avatar_movement"])
        move_card.add_editable(
            _choice_control(list(vp.MOVEMENT_PATTERNS), s["avatar_movement"]),
            lambda v: apply_setting("avatar_movement", v),
        )

        respect_card = _Card(box, "Respect notification corner",
                             "Keep the corner loop out of the corner you get system notifications in.",
                             "yes" if s["avatar_respect_corner"] else "no")
        respect_card.add_editable(
            Gtk.Switch(active=s["avatar_respect_corner"]),
            lambda v: apply_setting("avatar_respect_corner", v),
        )

        corner_card = _Card(box, "Notification corner",
                            "The corner the avatar should avoid when respect is on.",
                            s["avatar_notification_corner"])
        corner_card.add_editable(
            _choice_control(list(vp.NOTIFICATION_CORNERS), s["avatar_notification_corner"]),
            lambda v: apply_setting("avatar_notification_corner", v),
        )

        approval_card = _Card(box, "Approval box",
                              "Tahuti can show its approval request beside the avatar.",
                              "on" if s["avatar_approval_box"] else "off")
        approval_card.add_editable(
            Gtk.Switch(active=s["avatar_approval_box"]),
            lambda v: apply_setting("avatar_approval_box", v),
        )

        # Lifecycle toggles
        for key, title, desc in (
            ("avatar_show_on_talk", "Show on talk",
             "Pop up while L or R talk button is held."),
            ("avatar_stay_working", "Stay while working",
             "Keep showing while the agent is working after you release the button."),
            ("avatar_fade_done", "Fade when done",
             "Fade out once the agent's result lands."),
            ("avatar_popup_approval", "Pop up on approval",
             "Pop up with the approval box whenever approval is needed."),
        ):
            value = bool(s[key])
            card = _Card(box, title, desc, "on" if value else "off")
            card.add_editable(
                Gtk.Switch(active=value),
                lambda v, k=key: apply_setting(k, v),
            )

    def _build_sensei_section(self):
        box = self._section_box("Sensei / Master AI")
        source, category, result = self._sensei_status
        if result is None:
            level, label, detail = WARN, "sensei bridge", "status unavailable"
        else:
            level, label, detail = result
        card = _Card(box, "Sensei bridge",
                     "Connection to the Sensei/Master AI bridge at 127.0.0.1:8791.",
                     f"{level} — {detail}")
        card.add_readonly(
            "No setup-side writer is wired for the Sensei bridge config yet; "
            "use the terminal wizard or edit the bridge config directly."
        )

    def _build_services_section(self):
        box = self._section_box("Services & Diagnostics")

        # Action bar card
        action_card = _Card(box, "Actions", "Refresh status, view diagnostics, or open the advanced terminal wizard.", "")
        refresh_btn = Gtk.Button(label="Refresh")
        refresh_btn.get_style_context().add_class("action-primary")
        refresh_btn.connect("clicked", self._on_refresh)
        diag_btn = Gtk.Button(label="Diagnostics output")
        diag_btn.connect("clicked", self._on_diagnostics)
        wizard_btn = Gtk.Button(label="Open full terminal wizard")
        wizard_btn.connect("clicked", self._on_wizard)
        action_card.controls.pack_start(refresh_btn, False, False, 0)
        action_card.controls.pack_start(diag_btn, False, False, 0)
        action_card.controls.pack_start(wizard_btn, False, False, 0)

        # Live doctor rows
        self._health_rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.pack_start(self._health_rows_box, False, False, 0)
        self._render_health_rows(box)

    def _render_health_rows(self, section_box):
        for child in self._health_rows_box.get_children():
            self._health_rows_box.remove(child)
        if not self._health:
            empty = Gtk.Label(label="No health data available.")
            empty.get_style_context().add_class("read-only")
            self._health_rows_box.pack_start(empty, False, False, 0)
            return
        for section in self._health:
            frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            frame.get_style_context().add_class("setting-card")
            frame.set_margin_bottom(6)
            title = Gtk.Label(label=section["category_label"])
            title.get_style_context().add_class("section-title")
            title.set_halign(Gtk.Align.START)
            frame.pack_start(title, False, False, 0)
            for row in section["rows"]:
                row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                level_lbl = Gtk.Label(label=row["level"])
                level_lbl.get_style_context().add_class(
                    f"level-{row['level'].lower()}"
                )
                level_lbl.set_size_request(40, -1)
                level_lbl.set_halign(Gtk.Align.START)

                name_lbl = Gtk.Label(label=row["label"])
                name_lbl.get_style_context().add_class("backend-name")
                name_lbl.set_size_request(120, -1)
                name_lbl.set_halign(Gtk.Align.START)

                detail_lbl = Gtk.Label(
                    label=textwrap.shorten(row["detail"], width=70, placeholder="...")
                )
                detail_lbl.get_style_context().add_class("backend-detail")
                detail_lbl.set_halign(Gtk.Align.START)
                detail_lbl.set_line_wrap(True)
                detail_lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                detail_lbl.set_tooltip_text(
                    f"{row['backend']} ({row['source']})\n{row['detail']}"
                )
                detail_lbl.set_hexpand(True)

                row_box.pack_start(level_lbl, False, False, 0)
                row_box.pack_start(name_lbl, False, False, 0)
                row_box.pack_start(detail_lbl, True, True, 0)
                frame.pack_start(row_box, False, False, 0)
            self._health_rows_box.pack_start(frame, False, False, 0)
        self._health_rows_box.show_all()

    def _on_refresh(self, _btn):
        self._set_status("Refreshing...")

        def run():
            try:
                data = self._gather_data()
                GLib.idle_add(self._rebuild_sections, data["settings"], data["health"], data["sensei"])
                GLib.idle_add(self._set_status, "Setup Center refreshed.")
            except Exception as exc:
                GLib.idle_add(self._set_status, f"Refresh failed: {exc}")

        threading.Thread(target=run, daemon=True).start()

    def _on_diagnostics(self, _btn):
        text = redacted_doctor_text()
        dialog = Gtk.Dialog(
            title="Diagnostics / support output",
            parent=self,
            flags=Gtk.DialogFlags.DESTROY_WITH_PARENT,
            buttons=("Close", Gtk.ResponseType.CLOSE),
        )
        dialog.set_modal(False)
        dialog.set_accept_focus(False)
        dialog.set_default_size(560, 360)
        content = dialog.get_content_area()
        scroll = Gtk.ScrolledWindow()
        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.set_cursor_visible(False)
        text_view.get_buffer().set_text(text)
        scroll.add(text_view)
        scroll.set_vexpand(True)
        content.pack_start(scroll, True, True, 0)
        content.show_all()
        dialog.connect("response", lambda d, response: d.destroy())
        dialog.show_all()

    def _on_wizard(self, _btn):
        self._set_status(open_wizard_in_terminal())

    def _on_close_button(self, _button):
        """Hide the reusable page from its visible Close control."""
        self.hide()

    def _on_drag_begin(self, _widget, event):
        """Let the user drag the page from its labeled header."""
        if event.button == 1:
            self.begin_move_drag(event.button, event.x_root, event.y_root, event.time)
        return True

    def _on_close(self, _widget, _event):
        self.hide()
        return True

    def _place_window(self):
        """Move the popup to a visible spot above the parent, without focus."""
        screen = self.get_screen()
        sw, sh = screen.get_width(), screen.get_height()
        parent_geom = None
        if self.parent_window is not None:
            try:
                px, py = self.parent_window.get_position()
                pw, ph = self.parent_window.get_size()
                parent_geom = (px, py, pw, ph)
            except Exception:
                pass
        x, y = _compute_setup_position(
            parent_geom, (self.WIDTH, self.HEIGHT), (sw, sh)
        )
        self.move(x, y)


# Singleton instance used by show_setup_panel.
_setup_panel_instance = None


def show_setup_panel(parent=None):
    """Create or return the singleton SetupCenter, mapped but not focus-raised."""
    global _setup_panel_instance
    if _setup_panel_instance is None:
        _setup_panel_instance = SetupCenter(parent)
    else:
        _setup_panel_instance.parent_window = parent
    _setup_panel_instance.show_all()
    _setup_panel_instance._place_window()
    # Keep the focus-disabled utility window visually above the keyboard and
    # normal application windows. ``raise_`` changes stacking only; unlike
    # present()/grab_focus(), it does not take the typing focus.
    get_window = getattr(_setup_panel_instance, "get_window", None)
    gdk_window = get_window() if callable(get_window) else None
    if gdk_window is not None:
        gdk_window.raise_()
    return _setup_panel_instance


# Backward-compatible alias for any code that looks for SetupPanel.
SetupPanel = SetupCenter


if __name__ == "__main__":
    win = SetupCenter()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()
