#!/usr/bin/env bash
# push-to-talk.sh — Xbox RT (F13 via AntiMicroX) → Groq Whisper → xdotool type

STT_ENDPOINT="http://localhost:8002/voice"
MIC_DEVICE="default"

echo "Push-to-talk dictation active"
echo "  Hold RT → speak → release → words typed into focused window"
echo "  Ctrl+C to quit"
echo ""

DISPLAY="${DISPLAY:-:0}" python3 - "$STT_ENDPOINT" "$MIC_DEVICE" <<'PYEOF'
import sys, subprocess, os, tempfile, json, threading, time, random
from pynput import keyboard

endpoint = sys.argv[1]
mic      = sys.argv[2]

recording = False
rec_proc  = None
tmpfile   = None
lock      = threading.Lock()

def start_recording():
    global recording, rec_proc, tmpfile
    with lock:
        if recording:
            return
        # Auto-space before dictation so words don't run together
        subprocess.run(
            ['xdotool', 'key', 'space'],
            env={**os.environ, 'DISPLAY': os.environ.get('DISPLAY', ':0')})
        tmpfile = tempfile.mktemp(suffix='.wav', dir='/tmp')
        rec_proc = subprocess.Popen(
            ['arecord', '-D', mic, '-f', 'S16_LE', '-r', '16000', '-c', '1',
             '--duration=120', tmpfile],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        recording = True
        print("  Recording...", flush=True)

def stop_and_send():
    global recording, rec_proc, tmpfile
    with lock:
        if not recording:
            return
        rec_proc.terminate()
        rec_proc.wait()
        recording = False

    if not tmpfile or not os.path.exists(tmpfile) or os.path.getsize(tmpfile) < 2000:
        print("  Too short.", flush=True)
        tmpfile = None
        return

    # Solid indicator — random emoji each time, stays while processing
    emojis = ['🎙️','🧠','⚡','🔥','🎯','💡','🌊','🎵','🚀','✨','🎤','💬','🌀','🎶','💫']
    icon = random.choice(emojis)
    env = {**os.environ, 'DISPLAY': os.environ.get('DISPLAY', ':0')}
    subprocess.Popen(['notify-send', '-t', '4000', '-u', 'low', icon], env=env)

    print("  Sending...", flush=True)
    try:
        r = subprocess.run(
            ['curl', '-s', '-X', 'POST', endpoint,
             '-F', f'audio=@{tmpfile}',
             '-F', 'mode=transcribe_only',
             '-H', 'Accept: application/json'],
            capture_output=True, text=True, timeout=30)
        data = json.loads(r.stdout)
        text = data.get('text', '').strip()
        if text:
            print(f"  Typed: {text}", flush=True)
            subprocess.run(
                ['xdotool', 'type', '--clearmodifiers', '--', text],
                env={**os.environ, 'DISPLAY': os.environ.get('DISPLAY', ':0')})
        else:
            print("  (nothing heard)", flush=True)
    except Exception as ex:
        print(f"  Error: {ex}", flush=True)

    if tmpfile and os.path.exists(tmpfile):
        os.unlink(tmpfile)
    tmpfile = None

def on_press(key):
    if key == keyboard.Key.f13:
        threading.Thread(target=start_recording, daemon=True).start()

def on_release(key):
    if key == keyboard.Key.f13:
        threading.Thread(target=stop_and_send, daemon=True).start()

with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
    try:
        listener.join()
    except KeyboardInterrupt:
        print("\nStopped.")
PYEOF
