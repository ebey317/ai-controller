#!/bin/bash
# Start the full AI Controller stack IN THE CORRECT ORDER.
#
# The dictation listener (ptt-pynput) POSTs audio to voice-bridge on :8002.
# If it starts before voice-bridge has bound the port, the first recordings
# fail with "Expecting value: line 1 column 1" (empty response). So we:
#   1) software-replug the headset so the mic is live,
#   2) start voice-bridge FIRST and WAIT until :8002 actually answers,
#   3) only then start the dictation listener + the rest.
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"

# 1. Headset ready: combined profile creates both Xbox mic source AND headphone
#    sink so TTS plays through the controller headset.
export CONTROLLER_AUDIO_PROFILE=combined
bash "$DIR/reset-controller-audio.sh" || true

# 2. STT backend first — and block until it's genuinely serving.
systemctl --user start voice-bridge.service
READY=no
for _ in $(seq 1 20); do
    if curl -s -m2 http://localhost:8002/health >/dev/null 2>&1; then READY=yes; break; fi
    sleep 0.5
done
echo "voice-bridge ready: $READY"

# 3. Now everything that depends on it.
systemctl --user start \
    antimicrox-autoload.service \
    ptt-pynput.service \
    controller-legend.service \
    ai-slide-keyboard.service

echo "AI Controller started (sequence: audio reset -> voice-bridge[$READY] -> listener+UI)."
