#!/bin/bash
# Force-load xpad driver for Xbox controller if it doesn't bind automatically.
# Run manually or from launcher if controller LED is on but OS shows no input.
set -euo pipefail
if ! lsmod | grep -q '^xpad '; then
    sudo modprobe xpad
    sleep 1
fi
systemctl --user start antimicrox-autoload.service voice-bridge.service ptt-pynput.service controller-legend.service ai-slide-keyboard.service
