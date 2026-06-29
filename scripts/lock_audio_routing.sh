#!/bin/bash
# Lock default audio I/O to the Xbox controller headset/mic and move existing
# app streams (e.g. Discord) onto the headset sink.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Ensure the Xbox card is in combined profile so both sink and source exist.
export CONTROLLER_AUDIO_PROFILE=combined
bash "$SCRIPT_DIR/reset-controller-audio.sh" >/dev/null 2>&1 || true

# Wait for PulseAudio/PipeWire-pulse to be reachable.
for _ in {1..30}; do
    if pactl info >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

HEADSET_SINK="alsa_output.usb-Microsoft_Controller_3039373130383038333134313433-00.stereo-fallback"
HEADSET_SOURCE="alsa_input.usb-Microsoft_Controller_3039373130383038333134313433-00.mono-fallback"

# If the hardcoded sink/source names don't exist yet, fall back to whatever
# Microsoft/Xbox sink/source is currently visible.
if ! pactl list sinks short | awk '{print $2}' | grep -qx "$HEADSET_SINK"; then
    HEADSET_SINK=$(pactl list sinks short 2>/dev/null | grep -iE "Microsoft_Controller|Xbox" | awk '{print $2}' | head -1)
fi
if ! pactl list sources short | awk '{print $2}' | grep -qx "$HEADSET_SOURCE"; then
    HEADSET_SOURCE=$(pactl list sources short 2>/dev/null | grep -iE "Microsoft_Controller|Xbox" | grep -i "input" | awk '{print $2}' | head -1)
fi

# Set defaults.
[ -n "$HEADSET_SINK" ] && pactl set-default-sink "$HEADSET_SINK" || true
[ -n "$HEADSET_SOURCE" ] && pactl set-default-source "$HEADSET_SOURCE" || true
[ -n "$HEADSET_SINK" ] && pactl set-sink-volume "$HEADSET_SINK" 100% || true
[ -n "$HEADSET_SOURCE" ] && pactl set-source-volume "$HEADSET_SOURCE" 100% || true

# Move any existing playback streams onto the headset sink.
pactl list sink-inputs | awk '
    /^Sink Input #/{id=$3}
    /application.process.binary = "Discord"/{gsub(/#/,"",id); print id}
' | while read -r sid; do
    [ -n "$sid" ] && pactl move-sink-input "$sid" "$HEADSET_SINK" || true
done

echo "Audio locked to Xbox controller headset."
