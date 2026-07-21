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
LOCKFILE="/tmp/toggle_slide_keyboard.lock"

# Single-flight lock: a bouncy/impatient double-press on a cold start used to
# race two invocations against the same slow-starting PID detection below,
# with the loser sometimes hitting the "restart the service" fallback while
# the winner's SIGUSR1 was still in flight — net effect: show/hide/show
# thrashing, i.e. the button "not working" on some presses. flock -n makes
# the second press within ~2s a silent no-op instead of a second race.
exec 9>"$LOCKFILE"
flock -n 9 || exit 0

# Ensure the service is started (idempotent).
systemctl --user start "$SERVICE" 2>/dev/null

# Poll for the PID instead of a single fixed sleep — a cold GTK start can
# take longer than 0.2s to write the PID file, and the old fixed sleep made
# that case fall through to a disruptive full service restart every time.
PID=""
for _ in $(seq 1 20); do
    if [ -f "$PIDFILE" ]; then
        PID=$(cat "$PIDFILE" 2>/dev/null)
    fi
    if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
        PID=$(pgrep -f 'slide_keyboard.py --show' | head -n1)
    fi
    [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null && break
    PID=""
    sleep 0.1
done

if [ -n "$PID" ]; then
    kill -USR1 "$PID"
else
    # Last resort: restart the service.
    systemctl --user restart "$SERVICE"
fi
