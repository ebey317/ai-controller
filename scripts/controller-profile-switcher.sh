#!/usr/bin/env bash
# controller-profile-switcher.sh
# Watches the focused window and swaps the AntiMicroX profile to match.
#   Browser focused  → ai-browser.amgp  (web navigation)
#   Media player     → ai-iptv.amgp     (TV remote)
#   Anything else    → ai-desktop.amgp  (dictation + mouse/keyboard)
#
# Run via: nohup DISPLAY=:0 bash ~/scripts/controller-profile-switcher.sh &
# Or as the systemd user service antimicrox-autoload (swap ExecStart to this).

ANTIMICROX="/usr/bin/antimicrox"
PROFILE_DIR="${HOME}/.config/antimicrox"
export DISPLAY="${DISPLAY:-:0}"

# Stabilize Qt / AntiMicroX: disable network bearer polling (prevent SIGBUS)
export QT_BEARER_POLL_TIMEOUT=-1
export QT_NO_NETWORK_PROBING=1

# NOTE: removed SDL_JOYSTICK_DEVICE override. It forces js0 globally and
# can confuse AntiMicroX when the real mouse or other devices are present.

DESKTOP_PROFILE="$PROFILE_DIR/ai-desktop.amgp"
BROWSER_PROFILE="$PROFILE_DIR/ai-browser.amgp"
IPTV_PROFILE="$PROFILE_DIR/ai-iptv.amgp"

# Ensure F13-F18 have X11 keycodes (not in default keymap).
# Needed by ptt_pynput.py dictation and onboard keyboard scanner.
DISPLAY=:0 xmodmap -e "keycode 202 = F13" 2>/dev/null || true
DISPLAY=:0 xmodmap -e "keycode 197 = F14" 2>/dev/null || true
DISPLAY=:0 xmodmap -e "keycode 217 = F15" 2>/dev/null || true
DISPLAY=:0 xmodmap -e "keycode 219 = F16" 2>/dev/null || true
DISPLAY=:0 xmodmap -e "keycode 222 = F17" 2>/dev/null || true
DISPLAY=:0 xmodmap -e "keycode 230 = F18" 2>/dev/null || true

# Window-class regexes (lowercased) → profile category
is_browser() { [[ "$1" =~ (chrome|chromium|firefox|brave|edge|opera) ]]; }
is_media()   { [[ "$1" =~ (mpv|vlc|kodi|hypnotix|stremio|smplayer|celluloid) ]]; }

current_profile=""

# Kill both halves of a running AntiMicroX process (native binary or
# AppImage launcher + AppRun.wrapped child) and wait for them to actually
# die before returning.
kill_antimicrox() {
    pkill -x antimicrox 2>/dev/null
    for _ in $(seq 1 30); do
        pgrep -x antimicrox > /dev/null 2>&1 || break
        sleep 0.2
    done
    sleep 0.5
}

controller_present() {
    [[ -c /dev/input/js0 ]]
}

# Sentinel file used to signal the main loop that the controller just
# changed state. Both the watch thread and main loop run in the same
# process, but background functions can't mutate parent variables.
touch_controller_changed() {
    touch /tmp/controller_state_changed
}

clear_controller_changed() {
    rm -f /tmp/controller_state_changed
}

# Restart antimicrox immediately when controller node changes, so the user
# doesn't have to wait for the main 1-second loop.
watch_controller() {
    local last_state="unknown"
    while true; do
        if controller_present; then
            if [[ "$last_state" != "present" ]]; then
                touch_controller_changed
                last_state="present"
            fi
        else
            if [[ "$last_state" != "absent" ]]; then
                kill_antimicrox
                touch_controller_changed
                echo "$(date '+%H:%M:%S') → controller unplugged, stopped"
                last_state="absent"
            fi
        fi
        sleep 1
    done
}

ANTIMICROX_PIDFILE="/tmp/antimicrox_profile_switcher.pid"
ALIVE_MISS=0

antimicrox_alive() {
    if [[ -s "$ANTIMICROX_PIDFILE" ]]; then
        local pid
        pid=$(cat "$ANTIMICROX_PIDFILE")
        if kill -0 "$pid" 2>/dev/null && pgrep -x antimicrox > /dev/null 2>&1; then
            ALIVE_MISS=0
            return 0
        fi
    fi
    ALIVE_MISS=$((ALIVE_MISS + 1))
    # Require 3 consecutive misses before declaring dead (avoids transient pgrep timing)
    [[ $ALIVE_MISS -lt 3 ]]
}

load() {
    local profile="$1" label="$2"
    # If antimicrox died externally, reset so we restart even if profile path unchanged
    if [[ "$profile" == "$current_profile" ]] && ! antimicrox_alive; then
        current_profile=""
    fi
    [[ "$profile" == "$current_profile" ]] && return
    kill_antimicrox
    rm -f "$ANTIMICROX_PIDFILE"
    setsid nohup "$ANTIMICROX" --profile "$profile" --tray --eventgen xtest > /tmp/antimicrox.log 2>&1 &
    local loader=$!
    echo "$loader" > "$ANTIMICROX_PIDFILE"
    current_profile="$profile"
    echo "$label" > ~/.controller_current_profile
    DISPLAY=:0 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
        notify-send --replace-id=7001 -t 1000 -u low "🎮 ${label^^} MODE" 2>/dev/null || true
    echo "$(date '+%H:%M:%S') → $label ($(basename "$profile"))"
    # Give antimicrox time to initialize joystick device before next loop
    sleep 1
}

echo "Controller profile switcher started. Watching focused window and controller node..."
clear_controller_changed
watch_controller &
WATCH_PID=$!
trap 'kill $WATCH_PID 2>/dev/null; rm -f /tmp/controller_state_changed; kill_antimicrox; exit 0' EXIT INT TERM

while true; do
    if controller_present; then
        # User wants a single general profile across all apps. Always load desktop.
        load "$DESKTOP_PROFILE" "Desktop"
    else
        # No controller → make sure nothing is running, reset state
        if [[ -n "$current_profile" ]]; then
            kill_antimicrox
            current_profile=""
            echo "$(date '+%H:%M:%S') → controller unplugged, stopped"
        fi
    fi
    sleep 1
done
