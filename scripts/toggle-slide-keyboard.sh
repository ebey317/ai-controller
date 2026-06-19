#!/bin/bash
# toggle-slide-keyboard.sh — View button (button 5) on-screen keyboard toggle.
# Replaces the broken onboard-toggle.sh.
#
# First press: launches slide_keyboard.py (visible immediately via --show)
# Second press: sends SIGUSR1 to toggle it hidden
# Subsequent presses: SIGUSR1 toggles show/hide
# If the process died, relaunches it.
export DISPLAY=:0
SCRIPT="/home/elijah/scripts/slide_keyboard.py"
PIDFILE="/tmp/slide_keyboard.pid"

if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE" 2>/dev/null)
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        # Process alive — toggle via SIGUSR1
        kill -USR1 "$PID"
        exit 0
    fi
fi

# No running process — launch fresh with --show so it appears immediately
/usr/bin/python3 "$SCRIPT" --show &
echo $! > "$PIDFILE"