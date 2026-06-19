#!/usr/bin/env python3
import sys, subprocess, os, tempfile, json, threading, wave, struct, time, re
from datetime import datetime
import urllib.request
import urllib.error
from pynput import keyboard

endpoint = "http://localhost:8002/voice"
BRIDGE_URL = os.environ.get("BRIDGE_URL", "http://127.0.0.1:8080")
SENSEI_SESSION = os.environ.get("SENSEI_SESSION", "focus-engine")


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
_TYPE_SETTLE_MS = 300
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
