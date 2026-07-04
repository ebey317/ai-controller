#!/bin/bash
# toggle-slide-keyboard.sh — View button (button 5) on-screen keyboard toggle.
# Relies on the ai-slide-keyboard.service to keep the keyboard alive.
#
# First press: ensures the service is running and sends SIGUSR1 to show it.
# Subsequent presses: SIGUSR1 toggles show/hide.
# If the process died, the service will restart it.
export DISPLAY="${DISPLAY:-:0}"
SERVICE="ai-slide-keyboard.service"
PIDFILE="/tmp/slide_keyboard.pid"

# Ensure the service is started (idempotent).
systemctl --user start "$SERVICE" 2>/dev/null

# Give the service a moment to spawn the keyboard.
sleep 0.2

# Prefer the PID file written by slide_keyboard.py; fall back to pgrep.
PID=""
if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE" 2>/dev/null)
fi
if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
    PID=$(pgrep -f 'slide_keyboard.py --show' | head -n1)
fi

if [ -n "$PID" ]; then
    kill -USR1 "$PID"
else
    # Last resort: restart the service.
    systemctl --user restart "$SERVICE"
fi
