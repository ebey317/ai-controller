#!/bin/bash
# Hermes TTS playback wrapper — routes audio to the configured output device.
# Reads AUDIO_OUTPUT from ~/.config/ai-controller/config.env if available.

AUDIO_FILE="$1"

if [[ -z "$AUDIO_FILE" ]]; then
    echo "Usage: $0 <audio_file.mp3>"
    exit 1
fi

if [[ ! -f "$AUDIO_FILE" ]]; then
    echo "Error: File not found: $AUDIO_FILE"
    exit 1
fi

CONFIG_FILE="${HOME}/.config/ai-controller/config.env"
AUDIO_OUTPUT=""
if [[ -f "$CONFIG_FILE" ]]; then
    # shellcheck source=/dev/null
    AUDIO_OUTPUT=$(set -a; source "$CONFIG_FILE" 2>/dev/null; echo "${AUDIO_OUTPUT:-}")
fi

if [[ -n "$AUDIO_OUTPUT" ]]; then
    SINK="pulse/${AUDIO_OUTPUT}"
    mpv --no-video --audio-device="$SINK" --af=lowpass=f=3000 "$AUDIO_FILE" 2>/dev/null
else
    # No output device configured — let PulseAudio use the default sink.
    mpv --no-video --af=lowpass=f=3000 "$AUDIO_FILE" 2>/dev/null
fi