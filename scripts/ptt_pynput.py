#!/usr/bin/env python3
import sys, subprocess, os, tempfile, json, threading, wave, struct, time, re, random, fcntl
from datetime import datetime
import urllib.request
import urllib.error
from pynput import keyboard
import logging

# Persistent file log for dictation pipeline debugging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("/tmp/ptt-pynput.log"),
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


def _type_text_fast(text: str, mode: str = "pro") -> None:
    """Type text into the focused window.

    xdotool type is the only method that works reliably across terminals,
    browsers, Discord, games, etc. Unicode modes are injected character-by-
    character, so they are slower than ASCII; clipboard paste was tried but
    is not reliable in the operator's target windows.
    """
    env = {**os.environ, 'DISPLAY': os.environ.get('DISPLAY', ':0')}
    # ASCII (PRO/CASUAL) can be fired as fast as possible.
    delay = _XDOTOOL_TYPE_DELAY_MS
    if any(ord(ch) >= 128 for ch in text):
        # Cursive (Mathematical Script) needs more time than bold/fullwidth.
        delay = 55 if mode == "bubbly" else 35
    subprocess.run(['xdotool', 'type', '--clearmodifiers',
                    f'--delay={delay}', '--', text],
                   env=env)


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


# Controller headset mic is 24000 Hz mono s16le. Capture at native rate
# through PulseAudio (parec) to avoid ALSA resampling artifacts.
SAMPLE_RATE = 24000
CHANNELS = 1
SAMPLE_WIDTH = 2  # s16le

# Debug: keep every recording so we can inspect failures later.
# Set PTT_DEBUG=1 in config.env to enable.
DEBUG_DIR = os.path.expanduser("/tmp/ptt-debug")
PTT_DEBUG = os.environ.get("PTT_DEBUG", load_env().get("PTT_DEBUG", "0")) == "1"
if PTT_DEBUG:
    os.makedirs(DEBUG_DIR, exist_ok=True)

recording = False
rec_proc = None
rawfile = None
wavfile = None
lock = threading.Lock()
_focus_window = None  # saved at recording start so we can restore focus before typing

# Debounce F13 chatter from the controller trigger.
_last_f13_time = 0.0
_DEBOUNCE_MS = 200
# Time for the user's finger to come off the controller before we inject keys.
_TYPE_SETTLE_MS = 50
# Type as fast as xdotool allows to minimize the window for controller interference.
_XDOTOOL_TYPE_DELAY_MS = 0
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
    player with --force-media-title=AI_TTS_BARGE. Piper voice packs play
    /tmp/ai_controller_tts.wav. Hermes' built-in TTS writes
    MP3s under /tmp/hermes_voice/ and plays them through ffplay (preferred) or
    aplay (Linux fallback). All of those are killed here so RT -> F13 always
    barges in on agent speech.
    """
    # Controller voice stack: tagged mpv.
    subprocess.run(['pkill', '-f', 'AI_TTS_BARGE'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Piper voice pack /tmp/ai_controller_tts.wav playback.
    subprocess.run(['pkill', '-f', 'ai_controller_tts'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Hermes built-in TTS: ffplay / aplay playing /tmp/hermes_voice/*.mp3.
    # The leading bracket pattern prevents pkill from matching its own argv.
    subprocess.run(['pkill', '-f', '[f]fplay.*hermes_voice'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['pkill', '-f', '[a]play.*hermes_voice'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
    with lock:
        if recording:
            return
        now = time.time()
        if (now - _last_f13_time) * 1000 < _DEBOUNCE_MS:
            return
        _last_f13_time = now
        # Mute any agent TTS before we open the mic.
        _mute_tts()
        # PulseAudio may have auto-suspended the Xbox headset source; wake it
        # with a short dummy stream so the real capture gets actual audio.
        _warmup_mic()
        # Save the currently focused window so we can restore focus before typing.
        # AntiMicroX or other apps may steal focus during recording.
        _focus_window = _active_window()
        # Auto-space before dictation so consecutive utterances don't run together.
        subprocess.run(['xdotool', 'key', 'space'],
                       env={**os.environ, 'DISPLAY': os.environ.get('DISPLAY', ':0')},
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
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


def stop_and_send():
    global recording, rec_proc, rawfile, wavfile, _last_f13_time
    with lock:
        if not recording:
            return
        now = time.time()
        if (now - _last_f13_time) * 1000 < _DEBOUNCE_MS:
            return
        _last_f13_time = now
        rec_proc.terminate()
        rec_proc.wait()
        if rec_proc.stdout:
            rec_proc.stdout.close()
        recording = False

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
            else:
                # Restore focus to the window that was active when recording
                # started — AntiMicroX or other apps may have stolen it.
                global _focus_window
                if _focus_window:
                    try:
                        subprocess.run(['xdotool', 'windowactivate', _focus_window],
                                       env={**os.environ, 'DISPLAY': os.environ.get('DISPLAY', ':0')},
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
                        time.sleep(0.05)
                    except Exception:
                        pass
                # Leading space was already injected before capture started.
                _type_text_fast(transcript, mode)
        else:
            log.info("(nothing heard)")
    except Exception as ex:
        log.error(f"Error: {ex}")
    finally:
        if show_indicator:
            _set_typing_state("idle")

    # Save a debug copy for later inspection (only if PTT_DEBUG=1).
    if PTT_DEBUG:
        try:
            ts = time.strftime("%Y%m%d_%H%M%S")
            safe_text = "".join(c if c.isalnum() else "_" for c in transcript)[:40] or "no_transcript"
            debug_path = os.path.join(DEBUG_DIR, f"ptt_{ts}_{duration:.1f}s_rms{int(rms)}_{safe_text}.wav")
            os.replace(wavfile, debug_path)
        except Exception:
            if wavfile and os.path.exists(wavfile):
                os.unlink(wavfile)
    else:
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

# Start evdev listener thread (does the actual F13 capture)
import threading as _thr
_evdev_thread = _thr.Thread(target=_run_evdev_listener, daemon=True, name="evdev-f13")
_evdev_thread.start()

with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
    try:
        listener.join()
    except KeyboardInterrupt:
        log.info("Stopped.")
