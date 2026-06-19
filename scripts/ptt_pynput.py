#!/usr/bin/env python3
import sys, subprocess, os, tempfile, json, threading, wave, struct, time, re
from datetime import datetime
import urllib.request
import urllib.error
from pynput import keyboard

endpoint = "http://localhost:8002/voice"
BRIDGE_URL = os.environ.get("BRIDGE_URL", "http://127.0.0.1:8080")
SENSEI_SESSION = os.environ.get("SENSEI_SESSION", "focus-engine")

# ---------------------------------------------------------------------------
# Transcription style toggle (controlled by slide_keyboard.py mode button)
# ---------------------------------------------------------------------------
MODE_FILE = os.path.expanduser("~/.config/ptt_mode")
VOCAB_FILE = os.path.expanduser("~/.config/ptt_vocabulary.json")



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
    """Return current PTT style mode: 'pro', 'bubbly', 'bold', or 'big'."""
    try:
        with open(MODE_FILE, "r", encoding="utf-8") as f:
            mode = f.read().strip().lower()
            if mode in ("pro", "bubbly", "bold", "big"):
                return mode
    except Exception:
        pass
    return "pro"


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


def _transform_text(text: str, mode: str) -> str:
    """Apply bubbly/bold/big style: styled letters + simple emoji keywords.

    PRO returns the raw transcript with no changes. BUBBLY uses italic Unicode
    instead of cursive because cursive glyphs are missing from many fonts,
    causing gaps and slow rendering. Italic is cursive-like but renders fast.
    """
    if mode == "pro":
        return text
    text = _add_emojis(text)
    if mode == "bubbly":
        text = _to_italic(text)
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
        print(f"  clipboard set failed: {exc}", flush=True)
        return False


def _queue_discord_text(text):
    """Save transcript for Discord so it can be pasted later."""
    os.makedirs(os.path.dirname(DISCORD_QUEUE), exist_ok=True)
    with open(DISCORD_QUEUE, "a", encoding="utf-8") as f:
        line = datetime.now().isoformat() + "	" + text + "\n"
        f.write(line)
    print("  Queued for Discord: " + text, flush=True)


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
_focus_window = None  # saved at recording start so we can restore focus before typing

# Debounce F13 chatter from the controller trigger.
_last_f13_time = 0.0
_DEBOUNCE_MS = 200
# Time for the user's finger to come off the controller before we inject keys.
_TYPE_SETTLE_MS = 50
# Type as fast as xdotool allows to minimize the window for controller interference.
_XDOTOOL_TYPE_DELAY_MS = 0


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


def start_recording():
    global recording, rec_proc, rawfile, wavfile, _last_f13_time, _focus_window
    with lock:
        if recording:
            return
        now = time.time()
        if (now - _last_f13_time) * 1000 < _DEBOUNCE_MS:
            return
        _last_f13_time = now
        # Save the currently focused window so we can restore focus before typing.
        # AntiMicroX or other apps may steal focus during recording.
        _focus_window = _active_window()
        # Auto-space before dictation
        subprocess.run(['xdotool', 'key', 'space'],
                       env={**os.environ, 'DISPLAY': os.environ.get('DISPLAY', ':0')})
        rawfile = tempfile.mktemp(suffix='.raw', dir='/tmp')
        wavfile = tempfile.mktemp(suffix='.wav', dir='/tmp')
        rec_proc = subprocess.Popen(
            ['parec', '--device=alsa_input.usb-Microsoft_Controller_3039373130383038333134313433-00.mono-fallback',
             '--rate', str(SAMPLE_RATE), '--channels', str(CHANNELS),
             '--format', 's16le', '--raw', rawfile],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        recording = True
        print("  Recording...", flush=True)


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


def _is_silence(path, rms_threshold=200):
    """Return True if the wav contains only silence (mic was physically off)."""
    _, rms = _wav_stats(path)
    return rms < rms_threshold


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
        print("  Too short — skipped.", flush=True)
        if wavfile and os.path.exists(wavfile):
            os.unlink(wavfile)
        wavfile = None
        return

    # Silence check: if mic was off (power button), the WAV is flat zeros
    if _is_silence(wavfile):
        print("  Silence detected (mic off?) — skipped.", flush=True)
        if wavfile and os.path.exists(wavfile):
            os.unlink(wavfile)
        wavfile = None
        return

    duration, rms = _wav_stats(wavfile)
    print(f"  Sending... ({duration:.2f}s RMS={rms:.1f})", flush=True)

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

        # Fast personal-vocabulary autocorrect (applies to PRO and BUBBLY)
        transcript = _apply_vocabulary(transcript)

        # Apply PRO / BUBBLY / BOLD / BIG style toggle (set by slide_keyboard.py mode button)
        mode = _load_ptt_mode()
        if mode in ("bubbly", "bold", "big") and transcript:
            transcript = _transform_text(transcript, mode)

        if response:
            print(f"  Response: {response}", flush=True)
        elif transcript:
            print(f"  Typed: {transcript}", flush=True)
            time.sleep(_TYPE_SETTLE_MS / 1000.0)
            if _is_discord_voice_window():
                # Discord voice channels have no focused text field; auto-typing
                # would fall through to the last text channel. Queue it instead.
                _set_clipboard_text(transcript)
                _queue_discord_text(transcript)
            elif _is_browser_window():
                result = _send_browser_text(transcript)
                if not result.get("ok"):
                    print(f"  Browser inject failed: {result.get('error')}", flush=True)
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
                # --window <id> (XSendEvent) is silently ignored by
                # gnome-terminal/VTE — confirmed 2026-06-16 (STT transcribed
                # correctly every time, nothing ever appeared on screen).
                # Plain `xdotool type`, no --window, uses XTestFakeKeyEvent
                # and goes to whatever has real focus.
                cmd = ['xdotool', 'type', '--clearmodifiers',
                       f'--delay={_XDOTOOL_TYPE_DELAY_MS}', '--', transcript]
                subprocess.run(cmd, env={**os.environ, 'DISPLAY': os.environ.get('DISPLAY', ':0')})
        else:
            print("  (nothing heard)", flush=True)
    except Exception as ex:
        print(f"  Error: {ex}", flush=True)

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
    if key == keyboard.Key.f13:
        threading.Thread(target=start_recording, daemon=True).start()


def on_release(key):
    if key == keyboard.Key.f13:
        threading.Thread(target=stop_and_send, daemon=True).start()


print("Push-to-talk dictation (pynput — Hold RT to speak, release to type)")
print("F13=dictation")
print("Ctrl+C to quit.\n")

with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
    try:
        listener.join()
    except KeyboardInterrupt:
        print("\nStopped.")
