#!/bin/bash
# ai-a-button-double-tap.sh — A button (button index 1) double-tap launcher.
#
# Runs as the FIRST slot on every A-button press, before settle_wiggle.sh
# and the click chain. Never delays the click: it just timestamps this
# press and compares to the last one. If the gap is under the threshold
# (rapid double-tap), it launches the AI Controller app in the background
# and clears the state so a third rapid press doesn't re-trigger it.
STATE_FILE="/tmp/.ai-controller-a-button-last-press"
THRESHOLD_MS=400
NOW_MS=$(date +%s%3N)

LAST_MS=0
[[ -f "$STATE_FILE" ]] && LAST_MS=$(cat "$STATE_FILE" 2>/dev/null || echo 0)
DELTA=$(( NOW_MS - LAST_MS ))

if (( LAST_MS > 0 && DELTA >= 0 && DELTA < THRESHOLD_MS )); then
    rm -f "$STATE_FILE"
    setsid -f /home/elijah/ai-controller/scripts/ai-controller-launcher.sh >/dev/null 2>&1 &
else
    echo "$NOW_MS" > "$STATE_FILE"
fi
