#!/bin/bash
# Quick audio loopback test for AI Controller.
# Records 3 seconds from the default PulseAudio source, then plays it back.
set -euo pipefail
export DISPLAY="${DISPLAY:-:0}"

echo "Recording 3 seconds..."
pactl list short sources | awk '{print $1, $2}'
echo ""
pactl list short sinks | awk '{print $1, $2}'
echo ""
parec --rate=24000 --channels=1 --format=s16le --raw /tmp/ai_audio_test.raw --latency=10 2>/dev/null &
REC=$!
sleep 3
kill "$REC" 2>/dev/null || true
wait "$REC" 2>/dev/null || true

python3 - <<'PY'
import wave
with open('/tmp/ai_audio_test.raw','rb') as rf, wave.open('/tmp/ai_audio_test.wav','wb') as wf:
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(24000)
    wf.writeframes(rf.read())
PY

echo "Playing back..."
mpv --no-video /tmp/ai_audio_test.wav

echo ""
echo "If you heard your voice, mic and speakers are good."
