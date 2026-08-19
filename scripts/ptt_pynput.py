#!/usr/bin/env python3
import sys, subprocess, os, tempfile, json, threading, wave, struct, time, re, random, fcntl, signal
from datetime import datetime
import urllib.request
import urllib.error
from pynput import keyboard
import logging
import logging.handlers

# Persistent file log for dictation pipeline debugging. Rotated -- this
# process runs for days at a time and an unbounded FileHandler here grows
# without limit for the life of the uptime.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(
            "/tmp/ptt-pynput.log", maxBytes=5 * 1024 * 1024, backupCount=2
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("ptt_pynput")

# Singleton: bail out if another instance is already running.
_singleton_fd = os.open("/tmp/ptt_pynput.lock", os.O_CREAT | os.O_RDWR)
try:
    fcntl.flock(_singleton_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    log.warning("Another ptt_pynput instance is already running; exiting.")
    sys.exit(0)

# Make shared helpers available regardless of cwd.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
from ai_controller_paths import config_dir, ensure_config_dir, load_env

endpoint = "http://localhost:8002/voice"

# Audio input source is configurable so the installer works on any machine.
_AUDIO_INPUT = load_env().get("AUDIO_INPUT", "")
_PAREC_DEVICE_ARGS = ["--device", _AUDIO_INPUT] if _AUDIO_INPUT else []
BRIDGE_URL = os.environ.get("BRIDGE_URL", "http://127.0.0.1:8002")
SENSEI_SESSION = os.environ.get("SENSEI_SESSION", "focus-engine")

# ---------------------------------------------------------------------------
# Transcription style toggle (controlled by slide_keyboard.py mode button)
# ---------------------------------------------------------------------------
ensure_config_dir()
MODE_FILE = os.path.join(config_dir(), "ptt_mode")
VOCAB_FILE = os.path.join(config_dir(), "ptt_vocabulary.json")
INPUT_TARGET_FILE = os.path.join(config_dir(), "ai_controller_input_target")
TYPING_STATE_FILE = "/tmp/ptt_typing_state"



# Unicode font maps (standalone, no dependencies)
_CURSIVE_LOWER = "𝓪𝓫𝓬𝓭𝓮𝓯𝓰𝓱𝓲𝓳𝓴𝓵𝓶𝓷𝓸𝓹𝓺𝓻𝓼𝓽𝓾𝓿𝔀𝔁𝔂𝔃"
_CURSIVE_UPPER = "𝓐𝓑𝓒𝓓𝓔𝓕𝓖𝓗𝓘𝓙𝓚𝓛𝓜𝓝𝓞𝓟𝓠𝓡𝓢𝓣𝓤𝓥𝓦𝓧𝓨𝓩"
_CURSIVE_MAP = {
    **{chr(0x61 + i): _CURSIVE_LOWER[i] for i in range(26)},
    **{chr(0x41 + i): _CURSIVE_UPPER[i] for i in range(26)},
}

_BOLD_LOWER = "𝐚𝐛𝐜𝐝𝐞𝐟𝐠𝐡𝐢𝐣𝐤𝐥𝐦𝐧𝐨𝐩𝐪𝐫𝐬𝐭𝐮𝐯𝐰𝐱𝐲𝐳"
_BOLD_UPPER = "𝐀𝐁𝐂𝐃𝐄𝐅𝐆𝐇𝐈𝐉𝐊𝐋𝐌𝐍𝐎𝐏𝐐𝐑𝐒𝐓𝐔𝐕𝐖𝐗𝐘𝐙"
_BOLD_MAP = {
    **{chr(0x61 + i): _BOLD_LOWER[i] for i in range(26)},
    **{chr(0x41 + i): _BOLD_UPPER[i] for i in range(26)},
}

# Sans-serif italic — cursive-like but renders faster and more complete than script
_ITALIC_LOWER = "𝘢𝘣𝘤𝘥𝘦𝘧𝘨𝘩𝘪𝘫𝘬𝘭𝘮𝘯𝘰𝘱𝘲𝘳𝘴𝘵𝘶𝘷𝘸𝘹𝘺𝘻"
_ITALIC_UPPER = "𝘈𝘉𝘊𝘋𝘌𝘍𝘎𝘏𝘐𝘑𝘒𝘓𝘔𝘕𝘖𝘗𝘘𝘙𝘚𝘛𝘜𝘝𝘞𝘟𝘠𝘡"
_ITALIC_MAP = {
    **{chr(0x61 + i): _ITALIC_LOWER[i] for i in range(26)},
    **{chr(0x41 + i): _ITALIC_UPPER[i] for i in range(26)},
}

# Fullwidth characters: visually wider/larger than normal ASCII
_FULLWIDTH_LOWER = "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"
_FULLWIDTH_UPPER = "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
_FULLWIDTH_DIGITS = "０１２３４５６７８９"
_FULLWIDTH_MAP = {
    **{chr(0x61 + i): _FULLWIDTH_LOWER[i] for i in range(26)},
    **{chr(0x41 + i): _FULLWIDTH_UPPER[i] for i in range(26)},
    **{chr(0x30 + i): _FULLWIDTH_DIGITS[i] for i in range(10)},
}

# Big standalone emoji keyword map — no LLM, no network, instant
_EMOJI_MAP = {
    # emotions
    "happy": "happy 😊", "sad": "sad 😢", "love": "love ❤️", "hate": "hate 😠",
    "heart": "heart ❤️", "excited": "excited 🤩", "bored": "bored 😐",
    "angry": "angry 😠", "mad": "mad 🤬", "tired": "tired 😴", "sleepy": "sleepy 😴",
    "sick": "sick 🤒", "surprised": "surprised 😲", "shocked": "shocked 😱",
    "confused": "confused 😕", "worried": "worried 😟", "proud": "proud 🥹",
    "embarrassed": "embarrassed 😳", "scared": "scared 😨", "lonely": "lonely 🥺",
    # reactions
    "lol": "lol 😂", "haha": "haha 😂", "lmao": "lmao 🤣", "wow": "wow 🤯",
    "omg": "omg 😱", "yay": "yay 🎉", "woo": "woo 🥳", "yikes": "yikes 😬",
    "ugh": "ugh 😩", "meh": "meh 😒", "hm": "hm 🤔", "hmm": "hmm 🤔",
    # greetings / goodbyes
    "hello": "hello 👋", "hi": "hi 👋", "hey": "hey 👋",
    "goodbye": "goodbye 👋", "bye": "bye 👋", "see you": "see you 👋",
    "good morning": "good morning 🌅", "good night": "good night 🌙",
    "thank you": "thank you 🙏", "thanks": "thanks 🙏", "please": "please 🥺",
    "sorry": "sorry 😔", "apologize": "apologize 🙇",
    # quality
    "fire": "fire 🔥", "cool": "cool 😎", "nice": "nice ✨", "great": "great 🎉",
    "awesome": "awesome 🤩", "amazing": "amazing 🤩", "perfect": "perfect 💯",
    "good": "good 👍", "bad": "bad 👎", "ok": "ok 👌", "okay": "okay 👌",
    "yes": "yes ✅", "no": "no ❌", "maybe": "maybe 🤷", "definitely": "definitely 💯",
    "check": "check ✅", "done": "done ✅", "finished": "finished ✅",
    # food / drink
    "hungry": "hungry 🍔", "coffee": "coffee ☕", "beer": "beer 🍺", "wine": "wine 🍷",
    "pizza": "pizza 🍕", "taco": "taco 🌮", "burger": "burger 🍔", "fries": "fries 🍟",
    "cake": "cake 🍰", "ice cream": "ice cream 🍦", "chocolate": "chocolate 🍫",
    "water": "water 💧", "tea": "tea 🍵", "breakfast": "breakfast 🍳", "dinner": "dinner 🍽️",
    # objects / tech
    "phone": "phone 📱", "computer": "computer 💻", "laptop": "laptop 💻",
    "game": "game 🎮", "controller": "controller 🎮", "music": "music 🎵",
    "book": "book 📚", "movie": "movie 🎬", "tv": "tv 📺", "money": "money 💰",
    "idea": "idea 💡", "light": "light 💡", "warning": "warning ⚠️", "rocket": "rocket 🚀",
    "time": "time ⏰", "date": "date 📅", "mail": "mail 📧", "email": "email 📧",
    # nature / animals
    "sun": "sun ☀️", "moon": "moon 🌙", "star": "star ⭐", "rain": "rain 🌧️",
    "snow": "snow ❄️", "fire": "fire 🔥", "ghost": "ghost 👻", "skull": "skull 💀",
    "cat": "cat 🐱", "dog": "dog 🐶", "bird": "bird 🐦", "fish": "fish 🐟",
    # events
    "party": "party 🎉", "birthday": "birthday 🎂", "congratulations": "congratulations 🎉",
    "weekend": "weekend 🎉", "work": "work 💼", "job": "job 💼",
}


def _load_ptt_mode() -> str:
    """Return current PTT style mode: 'pro', 'bubbly', 'casual', 'bold', or 'big'."""
    try:
        with open(MODE_FILE, "r", encoding="utf-8") as f:
            mode = f.read().strip().lower()
            if mode in ("pro", "bubbly", "casual", "bold", "big"):
                return mode
    except Exception:
        pass
    return "pro"


def _load_input_target() -> str:
    """Return input target: 'type' (default) or 'clipboard' (copy only)."""
    try:
        with open(INPUT_TARGET_FILE, "r", encoding="utf-8") as f:
            target = f.read().strip().lower()
            if target in ("type", "clipboard"):
                return target
    except Exception:
        pass
    return "type"


def _is_hermes_tui_window(window_pid: int) -> bool:
    """Return True if window_pid (or any descendant) hosts the Hermes TUI.

    The Hermes TUI runs inside a GNOME Terminal: a parent terminal process whose
    descendants include ``hermes --tui`` and ``tui_gateway.entry``. We need to
    activate the right terminal window before typing, otherwise xdotool sends
    dictation to whatever terminal last had focus.
    """
    try:
        import psutil  # type: ignore
    except ImportError:
        psutil = None

    def iter_cmdline_pids(patterns):
        for pid_str in os.listdir('/proc'):
            if not pid_str.isdigit():
                continue
            try:
                with open(f'/proc/{pid_str}/cmdline', 'rb') as f:
                    cmd = f.read().replace(b'\x00', b' ').decode('utf-8', 'replace')
                if any(p in cmd for p in patterns):
                    yield int(pid_str)
            except (OSError, ValueError):
                continue

    tui_pids = set(iter_cmdline_pids(['tui_gateway.entry', 'hermes --tui']))
    if not tui_pids:
        return False

    if psutil is not None:
        for tui_pid in tui_pids:
            try:
                proc = psutil.Process(tui_pid)
                for ancestor in proc.parents():
                    if ancestor.pid == window_pid:
                        return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False

    # /proc fallback without psutil
    def parents(pid: int):
        seen = set()
        while pid > 1 and pid not in seen:
            seen.add(pid)
            try:
                with open(f'/proc/{pid}/stat') as f:
                    parts = f.read().split()
                    # field 4 is ppid
                    pid = int(parts[3])
                    yield pid
            except (OSError, ValueError, IndexError):
                break

    for tui_pid in tui_pids:
        if window_pid in parents(tui_pid):
            return True
    return False


def _find_hermes_tui_window() -> str | None:
    """Find the GNOME Terminal window ID that hosts the Hermes TUI, or None.

    Multiple GNOME Terminal windows share the same process PID, so we can't
    distinguish them by PID. We match title patterns instead: the Hermes TUI
    title contains the model/provider separator `` · `` (e.g.
    ``Topic · kimi-k2.7-code · ~``). We avoid the "Recover closed session"
    terminal.
    """
    env = {**os.environ, 'DISPLAY': os.environ.get('DISPLAY', ':0')}
    try:
        out = subprocess.check_output(
            ['xdotool', 'search', '--onlyvisible', '--class', 'gnome-terminal'],
            env=env, text=True, timeout=3,
        ).strip()
    except Exception:
        return None
    candidates = []
    active = _active_window()
    for wid in out.split('\n'):
        wid = wid.strip()
        if not wid:
            continue
        try:
            name = subprocess.check_output(
                ['xdotool', 'getwindowname', wid], env=env, text=True, timeout=2
            ).strip()
        except Exception:
            continue
        # Reject the non-TUI terminal
        if 'recover closed session' in name.lower():
            continue
        # Hermes TUI titles contain the conversation/model separator.
        if ' · ' in name and ' · kimi' in name:
            return wid
        # Save fallback candidates: any gnome-terminal that isn't the recover one
        candidates.append(wid)
        # Prefer the currently active window if it looks like a TUI terminal
        if wid == active and ' · ' in name:
            return wid
    return candidates[0] if candidates else None


def _type_text_fast(text: str, mode: str = "pro", target_window: str | None = None) -> None:
    """Type text into the focused or target window.

    Plain ASCII (PRO mode) is injected with xdotool type for speed.
    Any non-ASCII output — styled Unicode or emoji — is copied to the
    X11 clipboard and pasted with Ctrl+Shift+V. xdotool type sends multi-byte
    characters one at a time and the Hermes TUI's terminal input buffer drops
    or merges bytes, so clipboard paste is the reliable path for Unicode.
    """
    env = {**os.environ, 'DISPLAY': os.environ.get('DISPLAY', ':0')}

    # Ensure the target window has focus before we type or paste.
    if target_window:
        subprocess.run(
            ['xdotool', 'windowactivate', target_window],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(0.03)

    if any(ord(ch) >= 128 for ch in text):
        # Unicode / emoji: clipboard paste. xdotool type per-character is
        # unreliable for multi-byte UTF-8 in the TUI terminal input buffer.
        if _set_clipboard_text(text):
            subprocess.run(
                ['xdotool', 'key', 'ctrl+shift+v'],
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        else:
            # Clipboard failed — fall back to slow character typing.
            delay = 55 if mode == "bubbly" else 35
            subprocess.run(
                ['xdotool', 'type', '--clearmodifiers', f'--delay={delay}', '--', text],
                env=env,
            )
        return

    # Plain ASCII: fast direct typing.
    cmd = ['xdotool', 'type', '--clearmodifiers', f'--delay={_XDOTOOL_TYPE_DELAY_MS}', '--', text]
    subprocess.run(cmd, env=env)


def _typing_hud(mode: str, text: str):
    """Launch a transient HUD for slow Unicode typing modes. Returns Popen handle or None."""
    if mode not in ("bubbly", "bold", "big"):
        return None
    if len(text) < 25:
        return None
    labels = {
        "bubbly": "✨  Typing cursive...",
        "bold": "𝐁  Typing bold...",
        "big": "Ｔ  Typing big...",
    }
    try:
        return subprocess.Popen(
            [sys.executable, os.path.join(os.path.dirname(__file__), "typing_hud.py"),
             labels.get(mode, "Typing..."), mode],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, 'DISPLAY': os.environ.get('DISPLAY', ':0')},
        )
    except Exception:
        return None


def _close_typing_hud(hud):
    """Close the typing HUD if it was opened."""
    if hud is None:
        return
    try:
        hud.terminate()
        hud.wait(timeout=1)
    except Exception:
        try:
            hud.kill()
        except Exception:
            pass


def _set_typing_state(state: str, mode: str = "") -> None:
    """Write typing state for slide_keyboard.py to consume.

    state: 'idle' or 'typing'
    """
    try:
        with open(TYPING_STATE_FILE, "w", encoding="utf-8") as f:
            f.write(f"{state}:{mode}" if mode else state)
    except Exception:
        pass


def _to_cursive(text: str) -> str:
    """Map ASCII letters to cursive script Unicode."""
    return "".join(_CURSIVE_MAP.get(ch, ch) for ch in text)


def _to_bold(text: str) -> str:
    """Map ASCII letters to bold mathematical Unicode."""
    return "".join(_BOLD_MAP.get(ch, ch) for ch in text)


def _to_italic(text: str) -> str:
    """Map ASCII letters to sans-serif italic Unicode."""
    return "".join(_ITALIC_MAP.get(ch, ch) for ch in text)


def _to_big(text: str) -> str:
    """Map ASCII letters/digits to fullwidth Unicode (visually larger)."""
    return "".join(_FULLWIDTH_MAP.get(ch, ch) for ch in text)


def _add_emojis(text: str) -> str:
    """Append an emoji when a known keyword is present. Preserves full text."""
    lowered = text.lower()
    # Longer phrases first so 'thank you' beats 'thanks'
    for phrase in sorted(_EMOJI_MAP, key=len, reverse=True):
        if phrase in lowered:
            # Strip the keyword prefix from the mapped value to get just the emoji
            emoji = _EMOJI_MAP[phrase][len(phrase):].strip()
            return f"{text} {emoji}"
    return text


# Casual-mode emoji boost: appended when no keyword emoji already fired.
_CASUAL_EMOJIS = ["👋", "☕", "😊", "✌️", "🙌", "🤙", "😎", "✨", "💯", "🔥", "🫡"]

# Fitzpatrick type-6 (dark) skin tone modifier for hand/person emojis.
# Face emojis (😊, 😎, etc.) do not support skin tones in Unicode.
_SKIN_TONE = "🏿"
_TONEABLE_BASES = {
    "\U0001F44B",  # 👋 waving hand
    "\u270C",       # ✌ victory hand
    "\U0001F64C",   # 🙌 raising hands
    "\U0001F919",   # 🤙 call me hand
    "\U0001F64F",   # 🙏 folded hands
    "\U0001F647",   # 🙇 person bowing
    "\U0001F44D",   # 👍 thumbs up
    "\U0001F44E",   # 👎 thumbs down
    "\U0001F44C",   # 👌 OK hand
    "\U0001F937",   # 🤷 shrug
}


def _apply_skin_tone(emoji: str) -> str:
    """Append the dark skin tone modifier to hand/person emojis."""
    out = []
    chars = list(emoji)
    i = 0
    while i < len(chars):
        ch = chars[i]
        if ch in _TONEABLE_BASES:
            out.append(ch)
            out.append(_SKIN_TONE)
            # Keep any emoji-variation selector after the tone.
            if i + 1 < len(chars) and chars[i + 1] == "\ufe0f":
                out.append("\ufe0f")
                i += 2
                continue
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# Apply skin tone to all hand/person emojis in the maps.
_EMOJI_MAP = {k: _apply_skin_tone(v) for k, v in _EMOJI_MAP.items()}
_CASUAL_EMOJIS = [_apply_skin_tone(e) for e in _CASUAL_EMOJIS]


def _casual_emoji_boost(text: str) -> str:
    """Append a casual emoji if the text doesn't already end with one."""
    if any(text.endswith(emoji) for emoji in _EMOJI_MAP.values()):
        return text
    return f"{text} {random.choice(_CASUAL_EMOJIS)}"


def _transform_text(text: str, mode: str) -> str:
    """Apply style to transcript based on active mode.

    PRO returns the raw transcript with no changes. BUBBLY uses italic Unicode.
    CASUAL lowercases everything and gets an extra emoji boost. BOLD and BIG
    use their respective Unicode letter blocks.
    """
    if mode == "pro":
        return text
    text = _add_emojis(text)
    if mode == "bubbly":
        text = _to_italic(text)
    elif mode == "casual":
        text = _casual_emoji_boost(text.lower())
    elif mode == "bold":
        text = _to_bold(text)
    elif mode == "big":
        text = _to_big(text)
    return text


def _load_vocabulary() -> dict[str, str]:
    """Load personal STT vocabulary corrections."""
    try:
        with open(VOCAB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("replacements", {})
    except Exception:
        return {}


_VOCAB_CACHE: dict[str, str] | None = None
_VOCAB_RE: re.Pattern | None = None


def _apply_vocabulary(text: str) -> str:
    """Fast personal-vocabulary autocorrect. No LLM, no network."""
    global _VOCAB_CACHE, _VOCAB_RE
    if _VOCAB_CACHE is None:
        _VOCAB_CACHE = _load_vocabulary()
        if _VOCAB_CACHE:
            # Build regex that matches any vocabulary key with word boundaries
            pattern = "|".join(re.escape(k) for k in _VOCAB_CACHE)
            _VOCAB_RE = re.compile(r"(?i)\b(" + pattern + r")\b")
        else:
            _VOCAB_RE = None
    if not _VOCAB_CACHE or _VOCAB_RE is None:
        return text

    def replace_match(m: re.Match) -> str:
        key = m.group(1).lower()
        return _VOCAB_CACHE.get(key, m.group(1))

    return _VOCAB_RE.sub(replace_match, text)


def _active_window_class():
    """Return WM_CLASS of active X11 window, or ''."""
    try:
        out = subprocess.check_output(
            ["xdotool", "getactivewindow", "getwindowclassname"],
            env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        return out.decode().strip().lower()
    except Exception:
        return ""


def _is_browser_window():
    cls = _active_window_class()
    return cls in ("google-chrome", "chrome", "firefox", "librewolf", "brave-browser", "chromium")



DISCORD_QUEUE = os.path.expanduser("~/.cache/ptt_discord_queue.txt")


def _is_discord_voice_window():
    """Return True if the active window is Discord (voice channel has no text field).

    Discord's WM_CLASS is 'discord'. The safest behavior in any Discord window is
    to avoid auto-typing into a possibly-wrong channel. The transcript is copied
    to the clipboard so the user can paste it where they want with Y (paste).
    """
    cls = _active_window_class()
    return cls == "discord"


def _set_clipboard_text(text):
    """Copy text to the X11 CLIPBOARD. Uses xclip if available; otherwise no-op."""
    try:
        subprocess.run(
            ["xclip", "-selection", "clipboard", "-in"],
            input=text.encode("utf-8"),
            env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=True,
        )
        return True
    except Exception as exc:
        log.warning(f"clipboard set failed: {exc}")
        return False


def _queue_discord_text(text):
    """Save transcript for Discord so it can be pasted later."""
    os.makedirs(os.path.dirname(DISCORD_QUEUE), exist_ok=True)
    with open(DISCORD_QUEUE, "a", encoding="utf-8") as f:
        line = datetime.now().isoformat() + "	" + text + "\n"
        f.write(line)
    log.info(f"Queued for Discord: {text}")


def _send_browser_text(text):
    """Send transcript to Sensei focus engine in the active browser tab."""
    url = f"{BRIDGE_URL}/extension/queue"
    escaped = json.dumps(text)
    code = f"window.__senseiFocus('set-text', {{text: {escaped}}})"
    body = {
        "session_id": SENSEI_SESSION,
        "actions": [
            {
                "kind": "BROWSER_JS",
                "target": code,
                "extras": {"source": "ptt_pynput", "command": "focus-set-text"},
            }
        ],
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            err = exc.read().decode("utf-8")
        except Exception:
            err = "unknown HTTP error"
        return {"ok": False, "error": err}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

# Controller headset mic is 24000 Hz mono s16le. Capture at native rate
# through PulseAudio (parec) to avoid ALSA resampling artifacts.
SAMPLE_RATE = 24000
CHANNELS = 1
SAMPLE_WIDTH = 2  # s16le

# Debug: keep every recording so we can inspect failures later.
DEBUG_DIR = os.path.expanduser("/tmp/ptt-debug")
os.makedirs(DEBUG_DIR, exist_ok=True)

recording = False
rec_proc = None
rawfile = None
wavfile = None
lock = threading.Lock()
# Prevents ghost recordings: stop_and_send must fully complete before
# start_recording can run again. Without this, a fast double-tap
# starts a new recording while the previous take's STT round-trip
# is still in flight — the mic opens and captures room audio that
# gets sent as a ghost utterance.
_processing_lock = threading.Lock()
_focus_window = None  # saved at recording start so we can restore focus before typing

# xone-gip card reset, deferred out of the capture window. The reset cycles the
# PulseAudio card profile off -> 0.7s -> on; running it while parec is attached
# (or about to attach) destroys the take. See _mute_tts / _fire_deferred_audio_reset.
_pending_audio_reset = False
_audio_reset_proc = None

# Debounce F13 chatter from the controller trigger.
_last_f13_time = 0.0
_DEBOUNCE_MS = 500
# Time for the user's finger to come off the controller before we inject keys.
_TYPE_SETTLE_MS = 50
# xdotool delay. 0 drops characters in the Hermes TUI's terminal input handling.
# 12ms is the fastest reliable value on this box for clean dictation.
_XDOTOOL_TYPE_DELAY_MS = 12
# Short accidental trigger presses often hallucinate these words from fan/mic noise.
_SHORT_HALLUCINATIONS = {
    "thank you", "thanks", "thank", "check", "yellow", "yep", "yup",
    "mm", "hmm", "um", "uh", "mhm", "okay", "ok",
}


def _active_window():
    """Return the currently focused X11 window ID, or None."""
    try:
        out = subprocess.check_output(
            ['xdotool', 'getactivewindow'],
            env={**os.environ, 'DISPLAY': os.environ.get('DISPLAY', ':0')},
            stderr=subprocess.DEVNULL, timeout=2)
        return out.decode().strip()
    except Exception:
        return None


def _build_wav(raw_path: str, wav_path: str):
    """Wrap raw s16le PCM in a WAV container."""
    with open(raw_path, "rb") as f:
        data = f.read()
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(data)


def _mute_tts():
    """Kill any playing TTS audio so the mic doesn't capture it.

    Controller voice stack (voice_bridge / hermes_tts_play.sh) tags its mpv
    player with --force-media-title=AI_TTS_BARGE. Legacy Piper dictation plays
    /tmp/ai_controller_tts.wav. Hermes' built-in TTS (provider: piper) writes
    MP3s under /tmp/hermes_voice/ and plays them through ffplay (preferred) or
    aplay (Linux fallback). All of those are killed here so RT -> F13 always
    barges in on agent speech.
    """
    # Tombstone for playback that does not exist yet. edge_tts generation is a
    # ~4s network round-trip; a press during that window finds no player to
    # kill, and speech starts AFTER the press — "I pressed RT and it kept
    # talking". voice_bridge._speak() compares this file's mtime against the
    # time its TTS request started and skips playback if the press came later.
    try:
        with open('/tmp/ai_tts_barge', 'w') as _f:
            _f.write(str(time.time()))
    except OSError:
        pass
    # NOTE: The old `pkill -SIGUSR2 -f tui_gateway.entry` was removed because
    # the Node.js TUI parent (signal-exit) treats SIGUSR2 as a termination
    # signal — every RT press killed the TUI. Hermes handles TTS barge-in
    # internally via the /tmp/ai_tts_barge tombstone + streaming abort events.
    # Controller voice stack: tagged mpv.
    #
    # NOT `pkill -f AI_TTS_BARGE`. That matches any process whose command line
    # merely mentions the tag — a shell running a script that greps for it, an
    # editor with the file open — and kills it. Observed killing live terminals
    # twice on 2026-07-30. Instead: only consider processes that ARE players
    # (by exact binary name), then check the tag in their cmdline. A shell is
    # never named `mpv`, so it can never be caught. Same contract as
    # tts_stop.sh; keep the two in sync.
    for _pid in os.listdir('/proc'):
        if not _pid.isdigit():
            continue
        try:
            with open(f'/proc/{_pid}/comm', 'r') as _f:
                if _f.read().strip() not in ('mpv', 'ffplay', 'aplay', 'paplay'):
                    continue
            with open(f'/proc/{_pid}/cmdline', 'rb') as _f:
                if b'AI_TTS_BARGE' not in _f.read():
                    continue
            os.kill(int(_pid), signal.SIGTERM)
        except (OSError, ValueError):
            continue
    # Legacy Piper /tmp/ai_controller_tts.wav playback.
    r1 = subprocess.run(['pkill', '-f', 'ai_controller_tts'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Hermes built-in TTS: ffplay / aplay playing /tmp/hermes_voice/*.mp3.
    # The leading bracket pattern prevents pkill from matching its own argv.
    r2 = subprocess.run(['pkill', '-f', '[f]fplay.*hermes_voice'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    r3 = subprocess.run(['pkill', '-f', '[a]play.*hermes_voice'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # voice_bridge's last-resort path uses speech-dispatcher. Do NOT pkill -f
    # 'spd-say' here: that pattern matches any command line merely mentioning
    # the string (a shell running a script that contains it, for instance) and
    # will kill unrelated processes. speech-dispatcher is a daemon that holds
    # the queued audio anyway, so cancelling the queue is both safer and the
    # only thing that actually stops the sound.
    r4 = subprocess.run(['spd-say', '-C'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # If we killed a live TTS player, reset the xone-gip audio to clear any wedge.
    #
    # r4 (`spd-say -C`) is DELIBERATELY excluded. It is a queue-cancel, not a
    # kill, and exits 0 whether or not anything was speaking — including on a
    # completely silent system. Including it made `killed` unconditionally True,
    # so every single F13 press launched reset-controller-audio.sh, which cycles
    # the PulseAudio card profile off -> (0.7s) -> on. Recording opened parec
    # ~0.34s later, i.e. while the card was still `off`, so parec attached to a
    # source that did not exist and captured zero bytes. Every take then failed
    # the <16000-byte check as "Too short — skipped", including 9-second holds.
    # STT was dead for every press. Found 2026-08-06.
    #
    # Only the pkill results (r1-r3) are honest here: pkill exits 0 only when it
    # actually matched a process.
    killed = any(r.returncode == 0 for r in [r1, r2, r3])
    if killed:
        # DEFERRED, never fired here. The reset cycles the card profile
        # off -> 0.7s -> on, and start_recording() spawns parec ~0.3s after this
        # returns — i.e. while the capture source does not exist. Firing it in
        # the press path silently emptied the take on exactly the presses where
        # you barged in on the agent to say something.
        #
        # Instead: flag it, run it in _fire_deferred_audio_reset() once the mic
        # is closed, and have the next press block in _await_audio_reset() if
        # the cycle is still in flight.
        global _pending_audio_reset
        _pending_audio_reset = True
        log.info("killed a live TTS process; xone-gip reset deferred until mic closes")


def _fire_deferred_audio_reset():
    """Run a pending xone-gip card reset. Safe only once the mic is closed."""
    global _pending_audio_reset, _audio_reset_proc
    if not _pending_audio_reset:
        return
    _pending_audio_reset = False
    log.info("running deferred xone-gip audio reset")
    _audio_reset_proc = subprocess.Popen(
        ['bash', os.path.expanduser('~/ai-controller/scripts/reset-controller-audio.sh')],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _await_audio_reset(timeout=6.0):
    """Block until any in-flight card-profile cycle has finished.

    Without this the deferred reset just moves the race later: a fast second
    press would open parec while the previous take's reset was still cycling.
    """
    global _audio_reset_proc
    p = _audio_reset_proc
    if p is None:
        return
    if p.poll() is None:
        log.info("waiting for in-flight audio reset before opening mic")
        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            log.warning("audio reset still running after %.1fs — opening mic anyway", timeout)
    _audio_reset_proc = None


def _warmup_mic():
    """Send a short dummy capture stream to PulseAudio so an auto-suspended
    Xbox headset source resumes before the real recording starts."""
    if not _AUDIO_INPUT:
        return
    try:
        subprocess.run(
            ['parec', '--device', _AUDIO_INPUT,
             '--rate', str(SAMPLE_RATE), '--channels', str(CHANNELS),
             '--format', 's16le', '--raw'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=0.3)
    except Exception:
        pass


def start_recording():
    global recording, rec_proc, rawfile, wavfile, _last_f13_time, _focus_window
    # Block until any in-flight stop_and_send completes. This is the
    # ghost-recording fix: without it, a rapid second press opens the
    # mic while the previous take's STT round-trip is still running,
    # and the captured room audio becomes a phantom utterance.
    _processing_lock.acquire()
    try:
        with lock:
        if recording:
            return
        now = time.time()
        if (now - _last_f13_time) * 1000 < _DEBOUNCE_MS:
            return
        _last_f13_time = now
        # Mute any agent TTS before we open the mic.
        _mute_tts()
        # If a previous take's card reset is still cycling, the capture source
        # is missing right now. Wait it out rather than record silence.
        _await_audio_reset()
        # PulseAudio may have auto-suspended the Xbox headset source; wake it
        # with a short dummy stream so the real capture gets actual audio.
        _warmup_mic()
        # Save the currently focused window so we can restore focus before typing.
        # AntiMicroX or other apps may steal focus during recording.
        _focus_window = _active_window()
        # Auto-space before dictation so consecutive utterances don't run together.
        # Skip for Hermes TUI: each utterance is a separate message; a leading
        # space becomes junk in the input buffer.
        focus_title = ""
        if _focus_window:
            try:
                focus_title = subprocess.check_output(
                    ['xdotool', 'getwindowname', _focus_window],
                    env={**os.environ, 'DISPLAY': os.environ.get('DISPLAY', ':0')},
                    text=True, timeout=2,
                ).strip()
            except Exception:
                pass
        is_tui = (' · ' in focus_title and 'kimi' in focus_title.lower())
        if _focus_window is not None and not is_tui:
            subprocess.run(['xdotool', 'key', 'space'],
                           env={**os.environ, 'DISPLAY': os.environ.get('DISPLAY', ':0')},
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        else:
            log.info("skipping auto-space (TUI or no focus window)")
        fd, rawfile = tempfile.mkstemp(suffix='.raw', dir='/tmp')
        os.close(fd)
        fd, wavfile = tempfile.mkstemp(suffix='.wav', dir='/tmp')
        os.close(fd)
        rec_cmd = [
            'stdbuf', '-o0', 'parec',
            '--rate', str(SAMPLE_RATE), '--channels', str(CHANNELS),
            '--format', 's16le', '--raw',
        ] + _PAREC_DEVICE_ARGS
        rec_proc = subprocess.Popen(
            rec_cmd,
            stdout=open(rawfile, 'wb'), stderr=subprocess.DEVNULL)
        recording = True
        log.info("Recording...")
    finally:
        _processing_lock.release()


def _wav_stats(path):
    """Return (duration_seconds, rms) for a WAV file."""
    try:
        with wave.open(path, 'rb') as wf:
            raw = wf.readframes(wf.getnframes())
            rate = wf.getframerate()
        if len(raw) < 2 or rate == 0:
            return 0.0, 0.0
        samples = struct.unpack(f'<{len(raw)//2}h', raw[:len(raw) & ~1])
        rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
        return len(samples) / rate, rms
    except Exception:
        return 0.0, 0.0


def _is_silence(path, rms_threshold=100):
    """Return True if the wav contains only silence (mic was physically off)."""
    _, rms = _wav_stats(path)
    if rms < rms_threshold:
        log.info(f"Silence check RMS={rms:.1f} (threshold {rms_threshold})")
        return True
    return False


def _kill_recorder():
    """Stop the parec recorder and reap it. Must never leave a live capture.

    terminate() alone is not enough: if parec is blocked writing to its output
    file it can ignore SIGTERM long enough to outlive us, and an abandoned
    capture holds the controller mic open indefinitely. So: TERM, bounded
    wait, then KILL.
    """
    global rec_proc
    if rec_proc is None:
        return
    try:
        rec_proc.terminate()
        try:
            rec_proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            log.warning("parec ignored SIGTERM — killing it")
            rec_proc.kill()
            rec_proc.wait(timeout=1)
    except Exception as e:
        log.warning("recorder teardown failed: %s", e)
    finally:
        try:
            if rec_proc.stdout:
                rec_proc.stdout.close()
        except Exception:
            pass
        rec_proc = None


def _cleanup_temp_files():
    """Drop the raw/wav scratch files for a take we are discarding."""
    global rawfile, wavfile
    for attr in ('rawfile', 'wavfile'):
        path = globals().get(attr)
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
            globals()[attr] = None


def _reap_orphan_recorders():
    """Kill any parec left holding our capture device by an earlier crash.

    Runs at startup: a leaked recorder from a previous instance survives a
    service restart and will keep clicking the headset until reaped.
    """
    try:
        me = os.getpid()
        for pid in os.listdir('/proc'):
            if not pid.isdigit() or int(pid) == me:
                continue
            try:
                with open(f'/proc/{pid}/comm', 'r') as f:
                    if f.read().strip() != 'parec':
                        continue
                with open(f'/proc/{pid}/cmdline', 'rb') as f:
                    cmd = f.read()
                if _AUDIO_INPUT and _AUDIO_INPUT.encode() not in cmd:
                    continue
                os.kill(int(pid), signal.SIGKILL)
                log.warning("reaped orphaned parec pid=%s", pid)
            except (OSError, ValueError):
                continue
    except Exception:
        pass


def stop_and_send():
    global recording, rec_proc, rawfile, wavfile, _last_f13_time
    _processing_lock.acquire()
    try:
        with lock:
        if not recording:
            return
        now = time.time()
        if (now - _last_f13_time) * 1000 < _DEBOUNCE_MS:
            # Debounced chatter: discard this take. But NEVER return without
            # stopping the recorder first.
            #
            # This used to `return` here with parec still running. The process
            # then held the controller mic open forever — one was found alive
            # after 81 minutes on 2026-07-30. Because xone-gip shares a single
            # channel between mic and headset, a permanent 24 kHz capture
            # starves the playback buffers, which the kernel logs as
            # "gip_send_audio_samples: get buffer failed: -28" and which is
            # audible as random clicking/popping during TTS.
            #
            # Every fast trigger tap leaked another one, so it compounded.
            _kill_recorder()
            recording = False
            _fire_deferred_audio_reset()
            _cleanup_temp_files()
            return
        _last_f13_time = now
        _kill_recorder()
        recording = False
        # Mic is closed — safe to cycle the card now. Async: the STT round-trip
        # below runs in parallel, and the next press gates on _await_audio_reset.
        _fire_deferred_audio_reset()

    # Build WAV from raw PCM
    _build_wav(rawfile, wavfile)
    try:
        os.unlink(rawfile)
    except FileNotFoundError:
        pass
    rawfile = None

    # Minimum ~0.5s of audio
    if not wavfile or not os.path.exists(wavfile) or os.path.getsize(wavfile) < 16000:
        log.info("Too short — skipped.")
        if wavfile and os.path.exists(wavfile):
            os.unlink(wavfile)
        wavfile = None
        return

    # Silence check: if mic was off (power button), the WAV is flat zeros
    if _is_silence(wavfile):
        log.info("Silence detected (mic off?) — skipped.")
        if wavfile and os.path.exists(wavfile):
            os.unlink(wavfile)
        wavfile = None
        return

    duration, rms = _wav_stats(wavfile)
    log.info(f"Sending... ({duration:.2f}s RMS={rms:.1f})")

    # For Unicode modes, show the typing indicator immediately on trigger
    # release so the operator gets feedback while STT runs.
    mode = _load_ptt_mode()
    show_indicator = mode in ("bubbly", "bold", "big")
    if show_indicator:
        _set_typing_state("typing", mode)

    transcript = ""
    try:
        r = subprocess.run(
            ['curl', '-s', '-X', 'POST', endpoint,
             '-F', f'audio=@{wavfile}', '-F', 'mode=transcribe_only',
             '-H', 'Accept: application/json'],
            capture_output=True, text=True, timeout=30)
        data = json.loads(r.stdout)
        # transcribe_only returns {"text": ...}; execute returns {"transcript": ..., "response": ...}
        transcript = data.get('text', data.get('transcript', ''))
        response = data.get('response', data.get('error', '')).strip()

        # Short accidental trigger presses often produce hallucinated single words
        # from controller/mic noise. Skip them instead of typing garbage.
        clean = transcript.lower().strip(".,!?;:\"'")
        if duration < 1.5 and clean in _SHORT_HALLUCINATIONS:
            log.info(f"Skipped short-noise hallucination: '{transcript}'")
            transcript = ""

        # Fast personal-vocabulary autocorrect (applies to PRO and BUBBLY)
        transcript = _apply_vocabulary(transcript)

        # Apply PRO / BUBBLY / CASUAL / BOLD / BIG style toggle (set by slide_keyboard.py mode button)
        if mode in ("bubbly", "casual", "bold", "big") and transcript:
            transcript = _transform_text(transcript, mode)

        if response:
            log.info(f"Response: {response}")
        elif transcript:
            target = _load_input_target()

            # Only explicit clipboard target skips typing. Everything else goes
            # through xdotool type — clipboard auto-paste proved unreliable in
            # the operator's target windows.
            use_clipboard = (target == "clipboard")

            log.info(f"Output ({'clipboard' if use_clipboard else 'type'}): {transcript}")
            time.sleep(_TYPE_SETTLE_MS / 1000.0)

            if use_clipboard:
                _set_clipboard_text(transcript)
            elif _is_browser_window():
                result = _send_browser_text(transcript)
                if not result.get("ok"):
                    log.warning(f"Browser inject failed: {result.get('error')}")
            else:
                # Restore focus to the window that was active when recording
                # started — AntiMicroX or other apps may have stolen it.
                global _focus_window
                target_window = None
                if _focus_window:
                    try:
                        # If the saved focus window doesn't host the Hermes TUI,
                        # the user may have clicked into another terminal; find
                        # the actual TUI window instead.
                        focus_pid = int(subprocess.check_output(
                            ['xdotool', 'getwindowpid', _focus_window],
                            env={**os.environ, 'DISPLAY': os.environ.get('DISPLAY', ':0')},
                            text=True, timeout=2).strip())
                        if _is_hermes_tui_window(focus_pid):
                            target_window = _focus_window
                    except Exception:
                        pass

                if target_window is None:
                    target_window = _find_hermes_tui_window()
                    if target_window:
                        log.info(f"Hermes TUI window found: {target_window}")

                # Leading space was already decided before capture started.
                _type_text_fast(transcript, mode, target_window=target_window)
        else:
            log.info("(nothing heard)")
    except Exception as ex:
        log.error(f"Error: {ex}")
    finally:
        if show_indicator:
            _set_typing_state("idle")
    finally:
        _processing_lock.release()

    # Save a debug copy for later inspection.
    try:
        ts = time.strftime("%Y%m%d_%H%M%S")
        safe_text = "".join(c if c.isalnum() else "_" for c in transcript)[:40] or "no_transcript"
        debug_path = os.path.join(DEBUG_DIR, f"ptt_{ts}_{duration:.1f}s_rms{int(rms)}_{safe_text}.wav")
        os.replace(wavfile, debug_path)
    except Exception:
        if wavfile and os.path.exists(wavfile):
            os.unlink(wavfile)


def on_press(key):
    log.debug("Key pressed: %s (vk=%s)", key, getattr(key, 'vk', None))
    if key == keyboard.Key.f13:
        log.info("F13 PRESSED — starting recording")
        threading.Thread(target=start_recording, daemon=True).start()


def on_release(key):
    log.debug("Key released: %s (vk=%s)", key, getattr(key, 'vk', None))
    if key == keyboard.Key.f13:
        log.info("F13 RELEASED — stopping and sending")
        threading.Thread(target=stop_and_send, daemon=True).start()


log.info("Push-to-talk dictation (pynput — Hold RT to speak, release to type)")
log.info("F13=dictation")
log.info("Ctrl+C to quit.")

# ---------------------------------------------------------------------------
# Evdev fallback: pynput's X11 grab cannot see F13 injected by antimicrox
# (a uinput device).  Listen directly on the antimicrox keyboard event device.
# ---------------------------------------------------------------------------
def _make_fake_key():
    """Return the same singleton that pynput's own listener would pass to
    on_press/on_release for a physical F13. Using a bare _Fake class wouldn't
    equal `keyboard.Key.f13` under `==`, so the existing handler would drop it.
    keyboard.Key.f13 is a pynput Key enum member with vk=65482 — identical to
    what pynput's own x11 grab would deliver."""
    return keyboard.Key.f13

def _find_antimicrox_keyboard():
    """Re-enumerate /dev/input and return the antimicrox Keyboard Emulation device, or None.
    Called in a loop so that if antimicrox restarts and the uinput device gets
    re-numbered, we automatically re-attach without killing ptt_pynput."""
    try:
        import evdev  # type: ignore
    except ImportError:
        return None
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except (OSError, FileNotFoundError):
            continue
        if "antimicrox" in dev.name.lower() and "keyboard" in dev.name.lower():
            return dev
    return None

def _run_evdev_listener():
    """Listen for F13 on the antimicrox uinput keyboard device.

    NOTE: We deliberately do NOT use evdev.InputDevice.read_loop() — it
    silently fails to deliver events from certain uinput devices (proven on
    this box: raw os.read sees F13 press/release, evdev.read_loop sees
    nothing). Instead we do a manual select+os.read on the underlying fd
    and parse the 24-byte input_event struct ourselves."""
    import select as _select
    import struct as _struct
    f13 = _make_fake_key()
    backoff = 1.0
    while True:
        target = _find_antimicrox_keyboard()
        if target is None:
            log.warning("antimicrox keyboard device not found; retrying in %.1fs", backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 10.0)
            continue
        path = target.path
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as e:
            log.warning("Evdev fallback could not open %s: %s", path, e)
            time.sleep(1.0)
            continue
        # NOTE: We no longer grab the device exclusively. Instead, we read all
        # events passively and only act on F13. Other keys (Escape, Enter, etc.)
        # flow through to X11 normally so AntiMicroX button mappings work.
        backoff = 1.0
        log.info("Evdev fallback listening on %s (%s) for F13 (passive mode)", path, target.name)
        try:
            while True:
                r, _, _ = _select.select([fd], [], [], 0.5)
                if not r:
                    continue
                try:
                    buf = os.read(fd, 24)
                except BlockingIOError:
                    continue
                if len(buf) < 24:
                    continue
                # struct input_event { long tv_sec; long tv_usec; __u16 type; __u16 code; __s32 value; }
                _, _, ev_type, ev_code, ev_value = _struct.unpack("llHHi", buf)
                # EV_KEY = 1, KEY_F13 = 0xb7 (183)
                # Only handle F13 — let all other keys pass through to X11
                if ev_type == 1 and ev_code == 0xb7:
                    if ev_value == 1:
                        on_press(f13)
                    elif ev_value == 0:
                        on_release(f13)
        except OSError as e:
            # ENODEV or similar — antimicrox restarted and the uinput node
            # went away. Close the dead handle, loop back, re-enumerate.
            log.warning("Evdev listener lost device %s: %s — will re-enumerate", path, e)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            time.sleep(0.3)

# A leaked recorder from a previous instance survives a service restart and
# keeps the controller mic open, clicking the headset. Clear them before we
# start listening.
_reap_orphan_recorders()

# Start evdev listener thread (does the actual F13 capture)
import threading as _thr
_evdev_thread = _thr.Thread(target=_run_evdev_listener, daemon=True, name="evdev-f13")
_evdev_thread.start()

with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
    try:
        listener.join()
    except KeyboardInterrupt:
        log.info("Stopped.")
