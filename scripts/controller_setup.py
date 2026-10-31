#!/usr/bin/env python3
"""AI Controller setup -- the `hermes setup` / `Muse doctor` for this box.

    python3 scripts/controller_setup.py            interactive setup wizard
    python3 scripts/controller_setup.py doctor     health checks (plain report)
    python3 scripts/controller_setup.py telegram   Telegram feature audit
    python3 scripts/controller_setup.py register-commands
                                                   (re)register the bot's "/"
                                                   command menu with Telegram
    python3 scripts/controller_setup.py show       print current voice profiles

The wizard edits ONE shared file -- ~/.config/ai-controller/
voice-profiles.json (see voice_profiles.py) -- which the slide
keyboard's TTS and the avatars both read, so a voice tuned here is
tuned everywhere. The slide keyboard's SETUP button launches this
wizard in a terminal.

Stdlib-only at import; engines (Kokoro/Piper/edge-tts) are reached via
subprocesses so setup itself stays light.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import voice_profiles as vp  # noqa: E402
from ai_controller_paths import ai_controller_dir, config_dir, load_env  # noqa: E402

OK, WARN, FAIL = "OK", "WARN", "FAIL"

# ── Telegram bot command menu ─────────────────────────────────────────
# Every entry here is backed by a real handler in master-ai-cli's
# telegram_gateway.py: start/help/status/model/new are explicit
# CommandHandlers; the rest are Sensei REPL commands the gateway's
# generic command handler bridges (doctor, tasks, memory, standards,
# tips, keys, approved, git). Do NOT add a command here that no
# handler answers -- the menu is a promise.
EXPECTED_BOT_COMMANDS = [
    ("start", "open the bot / show help"),
    ("help", "show available commands"),
    ("status", "gateway uptime + current model"),
    ("model", "show or switch the answering model"),
    ("new", "start a fresh Sensei session"),
    ("doctor", "run Sensei doctor checks"),
    ("tasks", "show the Sensei task list"),
    ("memory", "show Sensei memory"),
    ("standards", "show Sensei standards"),
    ("tips", "show Sensei tips"),
    ("keys", "list configured key names"),
    ("approved", "show approved items"),
    ("git", "git status via Sensei"),
]
GATEWAY_HANDLED_COMMANDS = {name for name, _ in EXPECTED_BOT_COMMANDS}

SERVICES = [
    "ai-slide-keyboard.service",
    "voice-bridge.service",
    "master-ai-scheduler.service",
    "telegram-gateway.service",
]


def keys_file_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".master_ai_keys")


def read_key_names(path=None) -> set:
    """Key NAMES present in a KEY=VALUE file. Values are never returned."""
    path = path or keys_file_path()
    names = set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                names.add(line.partition("=")[0].strip())
    except FileNotFoundError:
        pass
    return names


def _read_key_value(name, path=None):
    """Internal only: fetch one secret value for API use. Never printed."""
    path = path or keys_file_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(name + "="):
                    return line.partition("=")[2].strip()
    except FileNotFoundError:
        pass
    return None


def _bot_api(token, method, payload=None, timeout=20):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def register_bot_commands(path=None):
    """setMyCommands with EXPECTED_BOT_COMMANDS. Returns (ok, message)."""
    token = _read_key_value("TELEGRAM_BOT_TOKEN", path)
    if not token:
        return False, "TELEGRAM_BOT_TOKEN not found in ~/.master_ai_keys"
    payload = {
        "commands": [
            {"command": name, "description": desc}
            for name, desc in EXPECTED_BOT_COMMANDS
        ]
    }
    try:
        result = _bot_api(token, "setMyCommands", payload)
    except Exception as e:  # network/API failure -- report, don't crash
        return False, f"setMyCommands failed: {e}"
    if result.get("ok"):
        return True, (
            f"registered {len(EXPECTED_BOT_COMMANDS)} commands: "
            + ", ".join("/" + n for n, _ in EXPECTED_BOT_COMMANDS)
        )
    return False, f"setMyCommands returned not-ok: {result.get('description')}"


def telegram_audit(path=None):
    """(level, label, detail) rows describing the bot's feature state."""
    rows = []
    names = read_key_names(path)
    if "TELEGRAM_BOT_TOKEN" in names:
        rows.append((OK, "bot token", "TELEGRAM_BOT_TOKEN present in "
                                      "~/.master_ai_keys (value not shown)"))
    else:
        rows.append((FAIL, "bot token", "TELEGRAM_BOT_TOKEN missing from "
                                        "~/.master_ai_keys"))
        return rows
    if "TELEGRAM_CHAT_ID" in names:
        rows.append((OK, "chat id", "TELEGRAM_CHAT_ID present"))
    else:
        rows.append((WARN, "chat id", "TELEGRAM_CHAT_ID missing -- outbound "
                                      "sends have no default destination"))

    token = _read_key_value("TELEGRAM_BOT_TOKEN", path)
    try:
        me = _bot_api(token, "getMe")
        if me.get("ok"):
            rows.append((OK, "bot identity",
                         f"@{me['result'].get('username')} answers getMe"))
        current = _bot_api(token, "getMyCommands")
        have = [c["command"] for c in current.get("result", [])]
        want = [n for n, _ in EXPECTED_BOT_COMMANDS]
        if have == want:
            rows.append((OK, "command menu",
                         f"all {len(want)} commands registered"))
        else:
            rows.append((WARN, "command menu",
                         f"registered now: {have or 'none'} -- expected "
                         f"{want}; run: controller_setup.py register-commands "
                         f"(the gateway also re-registers its list at startup)"))
    except Exception as e:
        rows.append((WARN, "bot API", f"could not reach Telegram API: {e}"))

    rows.append((OK, "inline buttons",
                 "suggestive-thinking cards' ✅/👍 callbacks are answered by "
                 "telegram_gateway.py (pattern ^sg:) and the message is "
                 "edited with the outcome"))
    rows.append((OK, "message editing",
                 "telegram_client.edit_message_text is wired and used after "
                 "suggestion decisions"))
    rows.append(("INFO", "voice notes in",
                 "NOT wired: the gateway has no voice-message handler. "
                 "Wiring = MessageHandler(filters.VOICE) -> download file -> "
                 "existing STT (voice bridge / Whisper) -> free-text path. "
                 "~1 handler + download helper."))
    rows.append(("INFO", "voice replies out",
                 "NOT wired: replies are text-only; telegram_client has no "
                 "sendVoice. Wiring = synthesize with the shared voice "
                 "profile (voice_profiles.py) -> sendVoice."))
    rows.append(("INFO", "files in",
                 "NOT wired: documents/photos sent to the bot are ignored. "
                 "Wiring = filters.Document/Photo handler -> save under an "
                 "inbox dir -> hand to Sensei as task context."))
    rows.append(("INFO", "files out",
                 "NOT wired: telegram_client has no sendDocument/sendPhoto. "
                 "Wiring = two small client methods (multipart POST)."))
    rows.append(("INFO", "reactions",
                 "NOT wired: setMessageReaction is unused. Wiring = one API "
                 "call (e.g. 👀 on receipt, 👍 on decision) in the gateway."))
    rows.append(("INFO", "forum topics",
                 "NOT applicable: the bot talks to a private chat; topics "
                 "exist only in forum supergroups."))
    return rows


# ── doctor ────────────────────────────────────────────────────────────

def check_profile_file(path=None):
    path = path or vp.profiles_path()
    if not os.path.exists(path):
        return (WARN, "voice profiles",
                f"{path} does not exist yet -- locked defaults are in "
                f"effect; run setup once to create it")
    try:
        data = vp.load_profiles(path)
    except Exception as e:
        return (FAIL, "voice profiles", f"{path} is invalid: {e}")
    tahuti = vp.get_avatar_profile(data, "tahuti")
    return (OK, "voice profiles",
            f"{path} valid; Tahuti = {tahuti['engine']}/{tahuti['voice']} "
            f"rate {tahuti['rate']} pitch {tahuti['pitch_semitones']}st; "
            f"avatar default "
            f"{'ON' if data['avatar']['enabled_by_default'] else 'OFF'} "
            f"scale {data['avatar']['scale']}")


def check_config_env(path=None):
    env = load_env(path)
    if not env:
        return (WARN, "config.env",
                "no config.env found/parsed under ~/.config/ai-controller "
                "(audio routing + API keys live there)")
    return (OK, "config.env",
            f"parses; {len(env)} keys present (values not shown)")


def check_piper():
    exe = shutil.which("piper") or os.path.expanduser("~/.local/bin/piper")
    if exe and os.path.exists(exe):
        return (OK, "piper", f"binary at {exe}")
    return (WARN, "piper", "piper binary not found -- Piper packs cannot "
                           "speak (edge-tts/Kokoro still can)")


def tahuti_tts_home() -> str:
    return os.path.join(os.path.expanduser("~"), "tahuti-tts")


def check_kokoro(home=None):
    home = home or tahuti_tts_home()
    model = os.path.join(home, "models", "kokoro-v1.0.onnx")
    voices_bin = os.path.join(home, "models", "voices-v1.0.bin")
    venv_py = os.path.join(home, "venv", "bin", "python")
    missing = [p for p in (model, voices_bin, venv_py) if not os.path.exists(p)]
    if missing:
        return (FAIL, "kokoro",
                "missing: " + ", ".join(missing) +
                " -- Tahuti's locked engine cannot speak")
    try:
        r = subprocess.run(
            [venv_py, "-c", "import kokoro_onnx, soundfile"],
            capture_output=True, timeout=60,
        )
        if r.returncode != 0:
            return (FAIL, "kokoro",
                    "venv python cannot import kokoro_onnx/soundfile: "
                    + r.stderr.decode(errors="replace")[-200:])
    except Exception as e:
        return (FAIL, "kokoro", f"import probe failed: {e}")
    return (OK, "kokoro", f"kokoro_onnx importable in {home}/venv; "
                          f"model + voices bin present")


def check_services():
    rows = []
    for svc in SERVICES:
        try:
            r = subprocess.run(
                ["systemctl", "--user", "is-active", svc],
                capture_output=True, text=True, timeout=10,
            )
            state = r.stdout.strip() or "unknown"
        except Exception as e:
            state = f"uncheckable ({e})"
        level = OK if state == "active" else WARN
        rows.append((level, f"service {svc}",
                     state + ("" if state == "active" else
                              " -- start with: systemctl --user start "
                              + svc)))
    return rows


def check_keyboard_voice():
    state_file = os.path.join(config_dir(), "ai_controller_voice")
    active = ""
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            active = f.read().strip()
    except FileNotFoundError:
        pass
    voices_dir = os.path.join(ai_controller_dir(), "voices")
    packs = []
    if os.path.isdir(voices_dir):
        for name in sorted(os.listdir(voices_dir)):
            if os.path.exists(os.path.join(voices_dir, name, "config.json")):
                packs.append(name)
    if not packs:
        return (WARN, "keyboard voice", f"no voice packs found in {voices_dir}")
    return (OK, "keyboard voice",
            f"packs: {', '.join(packs)}; toggle state: {active or '(unset)'}")


def run_doctor():
    rows = [check_profile_file(), check_config_env(), check_piper(),
            check_kokoro(), check_keyboard_voice()]
    rows += check_services()
    rows += [r for r in telegram_audit() if r[0] != "INFO"]
    return rows


def print_rows(rows):
    for level, label, detail in rows:
        print(f"  {level:<4}  {label}: {detail}")


# ── spoken previews ───────────────────────────────────────────────────

_KOKORO_HELPER = r"""
import json, sys
import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro
cfg = json.loads(sys.argv[1])
k = Kokoro(cfg["model"], cfg["voices_bin"])
samples, sr = k.create(cfg["text"], voice=cfg["voice"],
                       speed=cfg["rate"], lang="en-us")
st = float(cfg.get("pitch_semitones", 0.0))
if st:
    factor = 2 ** (st / 12.0)
    idx = np.arange(0, len(samples), factor)
    samples = np.interp(idx, np.arange(len(samples)),
                        samples).astype(samples.dtype)
sf.write(cfg["out"], samples, sr)
"""


def _play(path):
    if shutil.which("mpv"):
        subprocess.run(["mpv", "--no-video", path],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif shutil.which("aplay") and path.endswith(".wav"):
        subprocess.run(["aplay", "-q", path],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        print(f"(no player found -- sample written to {path})")


def _piper_model_for(voice_id):
    """Resolve a Piper model file for a pack id or bare model filename."""
    voices_dir = os.path.join(ai_controller_dir(), "voices")
    pack_dir = os.path.join(voices_dir, voice_id)
    model_name = voice_id
    cfg_path = os.path.join(pack_dir, "config.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                model_name = json.load(f).get("model", voice_id)
        except Exception:
            pass
    for candidate in (os.path.join(pack_dir, model_name),
                      os.path.join(os.path.expanduser("~/.local/share/piper"),
                                   model_name)):
        if os.path.exists(candidate):
            return candidate
    return None


def speak_preview(profile, name):
    """Synthesize + play the preview line for one profile. Never raises."""
    text = vp.preview_line(name)
    engine = profile.get("engine", "piper")
    rate = float(profile.get("rate", 1.0))
    pitch = float(profile.get("pitch_semitones", 0.0))
    out = os.path.join(tempfile.gettempdir(), f"aic_preview_{name}.wav")
    try:
        if engine == "kokoro":
            home = tahuti_tts_home()
            cfg = {
                "model": os.path.join(home, "models", "kokoro-v1.0.onnx"),
                "voices_bin": os.path.join(home, "models", "voices-v1.0.bin"),
                "text": text, "voice": profile["voice"], "rate": rate,
                "pitch_semitones": pitch, "out": out,
            }
            helper = os.path.join(tempfile.gettempdir(), "_aic_kokoro.py")
            with open(helper, "w", encoding="utf-8") as f:
                f.write(_KOKORO_HELPER)
            r = subprocess.run(
                [os.path.join(home, "venv", "bin", "python"), helper,
                 json.dumps(cfg)],
                capture_output=True, text=True, timeout=120,
            )
            if r.returncode != 0:
                print("Kokoro preview failed:", r.stderr.strip()[-300:])
                return False
        elif engine == "piper":
            model = _piper_model_for(profile["voice"])
            piper = shutil.which("piper") or os.path.expanduser(
                "~/.local/bin/piper")
            if not model or not os.path.exists(piper):
                print("Piper preview unavailable (model or binary missing).")
                return False
            r = subprocess.run(
                [piper, "--model", model, "--length-scale",
                 f"{1.0 / rate:.4f}", "--output_file", out],
                input=text.encode(), capture_output=True, timeout=120,
            )
            if r.returncode != 0:
                print("Piper preview failed:", r.stderr.decode(
                    errors="replace").strip()[-300:])
                return False
            if pitch:
                print("(note: Piper has no pitch control -- pitch applies "
                      "on Kokoro; the value is saved either way)")
        elif engine == "edge-tts":
            mp3 = out + ".mp3"
            py = sys.executable
            venv_py = os.path.join(ai_controller_dir(), ".venv", "bin",
                                   "python3")
            if os.path.exists(venv_py):
                py = venv_py
            r = subprocess.run(
                [py, "-m", "edge_tts", "--voice", profile["voice"],
                 f"--rate={round((rate - 1) * 100):+d}%",
                 "--text", text, "--write-media", mp3],
                capture_output=True, text=True, timeout=60,
            )
            if r.returncode != 0:
                print("edge-tts preview failed (needs network):",
                      r.stderr.strip()[-200:])
                return False
            out = mp3
        else:
            print(f"Unknown engine {engine!r}")
            return False
    except Exception as e:
        print(f"Preview failed: {e}")
        return False
    _play(out)
    return True


# ── interactive wizard ────────────────────────────────────────────────

def _ask(prompt, default=None):
    suffix = f" [{default}]" if default not in (None, "") else ""
    raw = input(f"{prompt}{suffix}: ").strip()
    return raw if raw else (default if default is not None else "")


def _ask_float(prompt, current, lo, hi):
    while True:
        raw = _ask(prompt, current)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            print(f"  enter a number between {lo} and {hi}")
            continue
        if lo <= value <= hi:
            return value
        print(f"  out of range -- {lo}..{hi}")


def _ask_yes(prompt, default=False):
    raw = input(f"{prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    if not raw:
        return default
    return raw.startswith("y")


def _edit_profile(name, profile):
    print(f"\n-- {name} -- current: engine={profile['engine']} "
          f"voice={profile['voice']} rate={profile['rate']} "
          f"pitch={profile['pitch_semitones']}st")
    print("  engines: " + ", ".join(vp.ENGINES))
    engine = _ask("engine", profile["engine"]).lower()
    if engine not in vp.ENGINES:
        print(f"  unknown engine -- keeping {profile['engine']}")
        engine = profile["engine"]
    voice = _ask("voice id", profile["voice"])
    rate = _ask_float("rate (0.5 slow .. 2.0 fast)", profile["rate"],
                      vp.RATE_MIN, vp.RATE_MAX)
    pitch = _ask_float("pitch shift, semitones (-12..+12)",
                       profile["pitch_semitones"], vp.PITCH_MIN, vp.PITCH_MAX)
    new = {"engine": engine, "voice": voice, "rate": rate,
           "pitch_semitones": pitch}
    if _ask_yes("Hear a preview line?", default=True):
        speak_preview(new, name)
        if not _ask_yes("Keep these settings?", default=True):
            print("  discarded.")
            return None
    return new


def wizard():
    print("=" * 62)
    print(" AI Controller -- setup")
    print(" One shared voice profile drives the keyboard + every avatar.")
    print(f" File: {vp.profiles_path()}")
    print("=" * 62)
    try:
        data = vp.load_profiles()
    except ValueError:
        print("Existing profile file is invalid -- starting from defaults; "
              "saving will repair it.")
        data = json.loads(json.dumps(vp.DEFAULT_PROFILES))
    if not os.path.exists(vp.profiles_path()):
        vp.save_profiles(data)
        print("(created the profile file with the locked defaults)")

    while True:
        print("\n  1) Tune an avatar voice (Tahuti + roster)")
        print("  2) Tune the keyboard voice")
        print("  3) Avatar overlay settings (default on/off, scale)")
        print("  4) Telegram (audit / register command menu)")
        print("  5) Run doctor")
        print("  0) Done")
        choice = _ask("choose", "0")
        if choice == "0":
            print("Setup closed. Everything you changed is already saved "
                  f"to {vp.profiles_path()}")
            return
        elif choice == "1":
            names = sorted(data["avatars"].keys())
            for i, n in enumerate(names, 1):
                p = data["avatars"][n]
                print(f"   {i}) {n}: {p['engine']}/{p['voice']} "
                      f"rate {p['rate']} pitch {p['pitch_semitones']}st")
            pick = _ask("avatar number, or type a new avatar name", "")
            if pick.isdigit() and 1 <= int(pick) <= len(names):
                name = names[int(pick) - 1]
            elif pick:
                name = pick.lower().replace(" ", "-")
            else:
                continue
            current = vp.get_avatar_profile(data, name)
            new = _edit_profile(name, current)
            if new is not None:
                data["avatars"][name] = new
                vp.save_profiles(data)
                print(f"  saved {name}.")
        elif choice == "2":
            kb = data["keyboard"]
            print(f"\n-- keyboard voice -- pack={kb['pack']} "
                  f"rate={kb['rate']} pitch={kb['pitch_semitones']}st")
            print("  (the keyboard's voice toggle on the slide keyboard "
                  "still switches packs live; setup sets the pack + tuning)")
            pack = _ask("pack (aria/joe/...)", kb["pack"])
            rate = _ask_float("rate multiplier", kb["rate"],
                              vp.RATE_MIN, vp.RATE_MAX)
            pitch = _ask_float("pitch shift, semitones",
                               kb["pitch_semitones"],
                               vp.PITCH_MIN, vp.PITCH_MAX)
            data["keyboard"] = {"pack": pack, "rate": rate,
                                "pitch_semitones": pitch}
            vp.save_profiles(data)
            # Keep voice_toggle's state file in step with the chosen pack.
            try:
                state = os.path.join(config_dir(), "ai_controller_voice")
                with open(state, "w", encoding="utf-8") as f:
                    f.write(pack)
            except Exception:
                pass
            print("  saved keyboard voice.")
        elif choice == "3":
            av = data["avatar"]
            print(f"\n-- avatar overlay -- default "
                  f"{'ON' if av['enabled_by_default'] else 'OFF'}, "
                  f"scale {av['scale']}")
            av["enabled_by_default"] = _ask_yes(
                "Show the avatar by default when the keyboard is up?",
                default=av["enabled_by_default"])
            av["scale"] = _ask_float("avatar scale", av["scale"],
                                     vp.SCALE_MIN, vp.SCALE_MAX)
            data["avatar"] = av
            vp.save_profiles(data)
            print("  saved avatar settings.")
        elif choice == "4":
            print_rows(telegram_audit())
            if _ask_yes("\nRegister the full command menu with Telegram "
                        "now?", default=False):
                ok, msg = register_bot_commands()
                print(("  OK   " if ok else "  FAIL ") + msg)
        elif choice == "5":
            print_rows(run_doctor())
        else:
            print("  pick 0-5")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("doctor", "--check", "check"):
        rows = run_doctor()
        print("AI Controller doctor")
        print_rows(rows)
        fails = sum(1 for r in rows if r[0] == FAIL)
        print(f"\n{'All hard checks passed.' if fails == 0 else str(fails) + ' check(s) FAILED -- see FAIL lines above.'}")
        return 1 if fails else 0
    if argv and argv[0] == "telegram":
        print("Telegram audit -- Master AI bot")
        print_rows(telegram_audit())
        return 0
    if argv and argv[0] == "register-commands":
        ok, msg = register_bot_commands()
        print(("OK   " if ok else "FAIL ") + msg)
        return 0 if ok else 1
    if argv and argv[0] == "show":
        print(json.dumps(vp.load_profiles(), indent=2))
        return 0
    if argv:
        print(__doc__)
        return 2
    if not sys.stdin.isatty():
        print("The setup wizard needs a terminal. Run it from the slide "
              "keyboard's SETUP button or: python3 scripts/"
              "controller_setup.py  (doctor: ... doctor)")
        return 2
    wizard()
    return 0


if __name__ == "__main__":
    sys.exit(main())
