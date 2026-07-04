#!/bin/bash
# Hermes TTS playback wrapper - routes audio to the configured output device.
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

# --force-media-title=AI_TTS_BARGE tags this as TTS so the trigger (RT) can
# barge-in and kill it without touching IPTV/video mpv.
# Resolve sink. If the configured sink doesn't exist, find the Xbox/Microsoft
# headset sink dynamically so TTS doesn't fall back to the wrong device.
SINK_NAME="$AUDIO_OUTPUT"
if [[ -n "$SINK_NAME" ]] && pactl list sinks short 2>/dev/null | awk '{print $2}' | grep -qx "$SINK_NAME"; then
    SINK="pulse/${SINK_NAME}"
else
    SINK_NAME=$(pactl list sinks short 2>/dev/null | grep -iE "Microsoft_Controller|Xbox" | awk '{print $2}' | head -1)
    if [[ -n "$SINK_NAME" ]]; then
        SINK="pulse/${SINK_NAME}"
    fi
fi

if [[ -n "$SINK" ]]; then
    mpv --no-video --force-media-title=AI_TTS_BARGE --audio-device="$SINK" --af=lowpass=f=3000 "$AUDIO_FILE" 2>/dev/null
else
    # No output device configured and no Xbox sink visible — use default sink.
    mpv --no-video --force-media-title=AI_TTS_BARGE --af=lowpass=f=3000 "$AUDIO_FILE" 2>/dev/null
fi
