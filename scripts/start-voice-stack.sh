#!/bin/bash
# Start just the STT/TTS voice stack (no UI).
set -euo pipefail
systemctl --user start voice-bridge.service ptt-pynput.service
echo "Voice stack started."
