#!/bin/bash
# Hermes TTS playback wrapper - routes audio to controller headphones via mpv
# Locked settings: pitch=-22Hz, rate=+12%, voice=en-US-AriaNeural

AUDIO_FILE="$1"
# Controller headphones (requires xone_gip_headset, currently blacklisted)
#SINK="pulse/alsa_output.usb-Microsoft_Controller_3039373130383038333134313433-00.stereo-fallback"
# Motherboard analog (3.5mm jack)
SINK="pulse/alsa_output.pci-0000_28_00.4.analog-stereo"

if [[ -z "$AUDIO_FILE" ]]; then
    echo "Usage: $0 <audio_file.mp3>"
    exit 1
fi

if [[ ! -f "$AUDIO_FILE" ]]; then
    echo "Error: File not found: $AUDIO_FILE"
    exit 1
fi

# Play with mpv, no video, to controller headphones
mpv --no-video --audio-device="$SINK" --af=lowpass=f=3000 "$AUDIO_FILE" 2>/dev/null