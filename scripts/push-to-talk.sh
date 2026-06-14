#!/usr/bin/env bash
# push-to-talk.sh — Xbox + headphone mic scheme
# Works with: RT controller trigger (F13) OR headphone inline mic button

# ═══════════════════════════════════════════════
#  USER PREFERENCES — EDIT THESE
# ═══════════════════════════════════════════════

# MIC TRIGGERS: both active simultaneously
#   F13             = Right Trigger (RT) on controller
#   XF86AudioMicMute = headphone inline mic button (Xbox headset scheme)
MIC_TRIGGERS="F13,XF86AudioMicMute"

# SEND BEHAVIOR:
#   "release" = sends automatically when you release the trigger
#   "review"  = shows transcription first, press Enter to send
SEND_BEHAVIOR="release"

# WHERE TO SEND:
STT_ENDPOINT="http://localhost:8000/voice"

# AUDIO DEVICE: "default" or specific ALSA device (arecord -l to list)
MIC_DEVICE="default"

# ═══════════════════════════════════════════════

detect_headphone_button() {
    echo "Press your headphone mic button now..."
    timeout 10 xev -event keyboard 2>/dev/null | grep -m1 'keysym' | \
        grep -oP 'keysym 0x[0-9a-f]+, (\S+)' | awk '{print $2}'
}

main() {
    if [[ "$1" == "--detect-button" ]]; then
        detect_headphone_button
        exit 0
    fi

    echo "Push-to-talk active (Xbox + headphone scheme)"
    echo "  Triggers : $MIC_TRIGGERS"
    echo "  Behavior : $SEND_BEHAVIOR"
    echo "  Endpoint : $STT_ENDPOINT"
    echo ""
    echo "Hold [RT] or [headphone mic button] to speak."

    # Ensure F13 has an X11 keycode
    DISPLAY="${DISPLAY:-:0}" xmodmap -e "keycode 202 = F13" 2>/dev/null || true

    DISPLAY="${DISPLAY:-:0}" python3 - "$MIC_TRIGGERS" "$SEND_BEHAVIOR" "$STT_ENDPOINT" "$MIC_DEVICE" <<'PYEOF'
import sys, subprocess, os, tempfile, time
from Xlib import X, display, XK

triggers_str = sys.argv[1]
behavior     = sys.argv[2]
endpoint     = sys.argv[3]
mic          = sys.argv[4]

# Known keysyms that need explicit hex values
KEYSYM_OVERRIDES = {
    'XF86AudioMicMute': 0x1008FFB2,
    'XF86AudioMute':    0x1008FF12,
    'XF86AudioRaiseVolume': 0x1008FF13,
    'XF86AudioLowerVolume': 0x1008FF11,
}

d = display.Display()
root = d.screen().root

# Resolve all triggers to keycodes
keycodes = set()
trigger_names = [t.strip() for t in triggers_str.split(',')]

for name in trigger_names:
    if name in KEYSYM_OVERRIDES:
        keysym = KEYSYM_OVERRIDES[name]
    else:
        keysym = XK.string_to_keysym(name)
    if keysym == 0:
        print(f"  WARNING: unknown key '{name}' — skipping")
        continue
    kc = d.keysym_to_keycode(keysym)
    if kc == 0:
        print(f"  WARNING: '{name}' has no keycode — skipping")
        continue
    keycodes.add(kc)
    print(f"  Wired: {name} → keycode {kc}")
    root.grab_key(kc, X.AnyModifier, True, X.GrabModeAsync, X.GrabModeAsync)

if not keycodes:
    print("ERROR: No valid trigger keys found. Run with --detect-button.")
    sys.exit(1)

d.flush()
print(f"\nListening on {len(keycodes)} trigger(s)...")

recording    = False
rec_proc     = None
tmpfile      = None
active_key   = None   # which keycode started the recording

while True:
    try:
        event = d.next_event()
    except KeyboardInterrupt:
        print("\nPush-to-talk stopped.")
        break

    if event.type == X.KeyPress and event.detail in keycodes and not recording:
        active_key = event.detail
        tmpfile = tempfile.mktemp(suffix='.wav', dir='/tmp')
        rec_proc = subprocess.Popen(
            ['arecord', '-D', mic, '-f', 'S16_LE', '-r', '16000', '-c', '1',
             '--duration=120', tmpfile],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        recording = True
        print("  Recording...", flush=True)

    elif event.type == X.KeyRelease and event.detail == active_key and recording:
        rec_proc.terminate()
        rec_proc.wait()
        recording  = False
        active_key = None

        if not tmpfile or not os.path.exists(tmpfile) or os.path.getsize(tmpfile) < 2000:
            print("  Too short — ignored.", flush=True)
            if tmpfile and os.path.exists(tmpfile):
                os.unlink(tmpfile)
            continue

        if behavior == "release":
            print("  Sending...", flush=True)
            result = subprocess.run(
                ['curl', '-s', '-X', 'POST', endpoint,
                 '-F', f'audio=@{tmpfile}', '-H', 'Accept: application/json'],
                capture_output=True, text=True, timeout=30
            )
            print(f"  Response: {result.stdout[:120]}", flush=True)
        else:
            result = subprocess.run(
                ['curl', '-s', '-X', 'POST', endpoint,
                 '-F', f'audio=@{tmpfile}', '-F', 'mode=transcribe_only',
                 '-H', 'Accept: application/json'],
                capture_output=True, text=True, timeout=30
            )
            text = result.stdout.strip()
            print(f"\n  Heard: {text}")
            print("  [ENTER=send, other=cancel]: ", end='', flush=True)
            confirm = sys.stdin.readline().strip()
            if confirm == '':
                subprocess.run(
                    ['curl', '-s', '-X', 'POST', endpoint,
                     '-F', f'text={text}', '-H', 'Accept: application/json'],
                    timeout=10
                )
                print("  Sent.", flush=True)
            else:
                print("  Cancelled.", flush=True)

        os.unlink(tmpfile)
        tmpfile = None

PYEOF
}

main "$@"
