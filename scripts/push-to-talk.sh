#!/usr/bin/env bash
# push-to-talk.sh — Mic trigger for AI controller profile
#
# ═══════════════════════════════════════════════
#  USER PREFERENCES — EDIT THESE
# ═══════════════════════════════════════════════

# MIC TRIGGER: What activates recording?
#   "F13"       = Right Trigger (RT) on controller (default in profiles)
#   "F14"       = any unused key you map
#   "XF86AudioMicMute" = headphone inline mic button (most headsets)
#   "auto"      = auto-detect first connected HID mic button
MIC_TRIGGER="F13"

# SEND BEHAVIOR: What happens after you speak?
#   "release"   = sends automatically when you release the trigger
#   "review"    = shows you the transcription first, you press Enter or A to send
SEND_BEHAVIOR="release"

# WHERE TO SEND: Which AI endpoint gets the text?
#   CLAF voice endpoint on Mary
STT_ENDPOINT="http://localhost:8000/voice"

# AUDIO DEVICE: Which mic to use?
#   "default"   = system default mic
#   "hw:1,0"    = specific ALSA device (run: arecord -l to see list)
MIC_DEVICE="default"

# ═══════════════════════════════════════════════
#  AUTO-DETECT HEADPHONE MIC BUTTON
# ═══════════════════════════════════════════════
# Most inline mic buttons on headphones register as XF86AudioMicMute
# Run this to find yours: xev | grep -A2 --line-buffered 'KeyPress'
# Then set MIC_TRIGGER above to match

detect_headphone_button() {
    echo "Listening for headphone mic button... press it now."
    timeout 10 xev -event keyboard 2>/dev/null | grep -m1 'keysym' | grep -oP 'keysym 0x[0-9a-f]+, (\S+)' | awk '{print $2}'
}

# ═══════════════════════════════════════════════
#  RECORD + SEND LOGIC
# ═══════════════════════════════════════════════

record_audio() {
    local outfile="$1"
    local max_sec="${2:-30}"
    arecord -D "$MIC_DEVICE" \
            -f S16_LE \
            -r 16000 \
            -c 1 \
            --duration="$max_sec" \
            "$outfile" 2>/dev/null &
    echo $!
}

send_to_claf() {
    local wavfile="$1"
    local response
    response=$(curl -s -X POST "$STT_ENDPOINT" \
                    -F "audio=@$wavfile" \
                    -H "Accept: application/json" 2>/dev/null)
    echo "$response"
}

review_and_confirm() {
    local text="$1"
    echo ""
    echo "╔══════════════════════════════════════════╗"
    echo "  TRANSCRIPTION:"
    echo "  $text"
    echo "╚══════════════════════════════════════════╝"
    echo "  Press ENTER or A (controller) to send"
    echo "  Press ESC or B to cancel"
    echo ""
    read -r -n1 confirm
    [[ "$confirm" == "" || "$confirm" == "a" ]]
}

# ═══════════════════════════════════════════════
#  MAIN: LISTEN FOR MIC TRIGGER
# ═══════════════════════════════════════════════

main() {
    # Special command: detect headphone button
    if [[ "$1" == "--detect-button" ]]; then
        detect_headphone_button
        exit 0
    fi

    echo "Push-to-talk active"
    echo "  Trigger   : $MIC_TRIGGER"
    echo "  Behavior  : $SEND_BEHAVIOR"
    echo "  Endpoint  : $STT_ENDPOINT"
    echo ""
    echo "Hold [$MIC_TRIGGER] to speak. Release to ${SEND_BEHAVIOR/release/send}${SEND_BEHAVIOR/review/review}."

    # Uses xbindkeys or xdotool to catch key events
    # This loop: catch keydown, record, catch keyup, process
    python3 - <<PYEOF
import subprocess, os, tempfile, sys, time

trigger = "${MIC_TRIGGER}"
behavior = "${SEND_BEHAVIOR}"
endpoint = "${STT_ENDPOINT}"
mic = "${MIC_DEVICE}"

print(f"Waiting for {trigger} keypress...")

while True:
    try:
        # Wait for keydown
        subprocess.run(
            ['xdotool', 'keydown', trigger],
            check=True, capture_output=True, timeout=300
        )

        # Start recording
        tmpfile = tempfile.mktemp(suffix='.wav', dir='/tmp')
        rec = subprocess.Popen(
            ['arecord', '-D', mic, '-f', 'S16_LE', '-r', '16000', '-c', '1',
             '--duration=30', tmpfile],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        print("  Recording... release to stop")

        # Wait for key release
        time.sleep(0.1)
        while True:
            try:
                result = subprocess.run(
                    ['xdotool', 'keyup', trigger],
                    capture_output=True, timeout=1
                )
                break
            except subprocess.TimeoutExpired:
                continue

        # Stop recording
        rec.terminate()
        rec.wait()

        if not os.path.exists(tmpfile) or os.path.getsize(tmpfile) < 1000:
            print("  Too short, ignoring.")
            continue

        # Send or review
        if behavior == "release":
            print("  Sending...")
            result = subprocess.run(
                ['curl', '-s', '-X', 'POST', endpoint,
                 '-F', f'audio=@{tmpfile}',
                 '-H', 'Accept: application/json'],
                capture_output=True, text=True, timeout=30
            )
            print(f"  Sent: {result.stdout[:80]}")
        else:
            # Review mode: transcribe first, show, confirm
            result = subprocess.run(
                ['curl', '-s', '-X', 'POST', endpoint,
                 '-F', f'audio=@{tmpfile}', '-F', 'mode=transcribe_only',
                 '-H', 'Accept: application/json'],
                capture_output=True, text=True, timeout=30
            )
            transcription = result.stdout.strip()
            print(f"\n  Heard: {transcription}")
            print("  [ENTER=send] [ESC=cancel]: ", end='', flush=True)
            confirm = sys.stdin.readline().strip()
            if confirm == '':
                # Re-send with 'send' mode
                subprocess.run(
                    ['curl', '-s', '-X', 'POST', endpoint,
                     '-F', f'text={transcription}',
                     '-H', 'Accept: application/json'],
                    timeout=10
                )
                print("  Sent.")
            else:
                print("  Cancelled.")

        os.unlink(tmpfile)

    except KeyboardInterrupt:
        print("\nPush-to-talk stopped.")
        break
    except Exception as e:
        print(f"  Error: {e}")
        time.sleep(1)
PYEOF
}

main "$@"
