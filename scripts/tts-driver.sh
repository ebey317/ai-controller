#!/bin/bash
# tts-driver.sh — pick which speaker AI Controller's voice comes out of.
#
#   tts-driver.sh                 # show the current driver and its device
#   tts-driver.sh --list          # list the drivers that are available now
#   tts-driver.sh hdmi            # sound through the monitor/TV (built-in)
#   tts-driver.sh headphones      # sound through the controller headset
#
# Why this exists
# ---------------
# The output choice used to be a raw PulseAudio sink name in config.env, which
# bakes the device serial in. That is unfriendly to say out loud, easy to get
# wrong by hand, and impossible to change without editing a file. This gives
# the choice a name you can use in a terminal or say out loud, and the serial
# is resolved at play time.
#
# Choosing "headphones" also brings the controller's output profile up, which
# is what makes a headset sink exist at all. Choosing anything else puts the
# controller back to microphone-only, so the output path the xone-gip driver is
# known to wedge is not left running when it is not wanted. Your microphone is
# untouched either way.
#
# Nothing here changes the system-wide default sink, so the rest of your audio
# keeps going where it was going.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TTS_CONTROL="$SCRIPT_DIR/tts_control.py"
PYTHON_BIN="$(command -v python3 || echo /usr/bin/python3)"

if [[ ! -f "$TTS_CONTROL" ]]; then
    echo "tts-driver: cannot find $TTS_CONTROL" >&2
    exit 1
fi

case "${1:---show}" in
    --show)
        "$PYTHON_BIN" "$TTS_CONTROL" status
        ;;
    --list)
        "$PYTHON_BIN" "$TTS_CONTROL" drivers
        ;;
    -h|--help)
        sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
        ;;
    *)
        if ! "$PYTHON_BIN" "$TTS_CONTROL" use "$1"; then
            echo "tts-driver: try --list to see the available drivers" >&2
            exit 2
        fi
        echo
        "$PYTHON_BIN" "$TTS_CONTROL" status
        ;;
esac
