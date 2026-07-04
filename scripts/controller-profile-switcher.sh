#!/usr/bin/env bash
# controller-profile-switcher.sh
# Watches the focused window and swaps the AntiMicroX profile to match.
#   Browser focused  → dont delete .gamecontroller.amgp  (web navigation)
#   Media player     → dont delete .gamecontroller.amgp  (TV remote)
#   YouTube TV       → dont delete .gamecontroller.amgp  (media controls)
#   Anything else    → dont delete .gamecontroller.amgp  (dictation + mouse/keyboard)
#
# Run via: nohup DISPLAY=:0 bash ~/scripts/controller-profile-switcher.sh &
# Or as the systemd user service antimicrox-autoload (swap ExecStart to this).
#
# ACTIVE PROFILE (2026-07-04): /home/elijah/ai-controller/profiles/dont delete .gamecontroller.amgp
# This is the ONLY profile in use - all modes use the same general-purpose layout.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$(dirname "$SCRIPT_DIR")"

# Prefer system AntiMicroX; fall back to bundled AppImage.
if command -v antimicrox &>/dev/null; then
    ANTIMICROX="$(command -v antimicrox)"
elif [[ -x "${INSTALL_DIR}/bin/antimicrox.AppImage" ]]; then
    ANTIMICROX="${INSTALL_DIR}/bin/antimicrox.AppImage"
else
    echo "ERROR: AntiMicroX not found. Install it or place antimicrox.AppImage in ${INSTALL_DIR}/bin/" >&2
    exit 1
fi

# AntiMicroX's canonical profile directory is where the user expects profiles
# to be loaded from. The installer symlinks our packaged profiles here, so the
# locked desktop profile always matches what AntiMicroX itself would show.
if [ -d "${HOME}/.config/antimicrox" ]; then
    PROFILE_DIR="${HOME}/.config/antimicrox"
else
    PROFILE_DIR="${INSTALL_DIR}/profiles"
fi
export DISPLAY="${DISPLAY:-:0}"

# Stabilize Qt / AntiMicroX: disable network bearer polling (prevent SIGBUS)
export QT_BEARER_POLL_TIMEOUT=-1
export QT_NO_NETWORK_PROBING=1

# NOTE: removed SDL_JOYSTICK_DEVICE override. It forces js0 globally and
# can confuse AntiMicroX when the real mouse or other devices are present.

DESKTOP_PROFILE="/home/elijah/ai-controller/profiles/dont delete .gamecontroller.amgp"
BROWSER_PROFILE="/home/elijah/ai-controller/profiles/dont delete .gamecontroller.amgp"
YOUTUBE_TV_PROFILE="/home/elijah/ai-controller/profiles/dont delete .gamecontroller.amgp"
IPTV_PROFILE="/home/elijah/ai-controller/profiles/dont delete .gamecontroller.amgp"

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

active_window_title() {
    xdotool getactivewindow getwindowname 2>/dev/null || true
}

is_youtube_tv() {
    local title="${1,,}"  # lower-case
    [[ "$title" =~ youtube[[:space:]]tv ]] || [[ "$title" =~ youtube\.com/tv ]]
}

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

# DEDUP FIX (2026-07-03): the old code only killed antimicrox when the controller
# DISAPPEARED. When the controller reappeared (e.g. xone reload re-enumerated it),
# the existing antimicrox kept running, then a second one was spawned — leaving
# two antimicrox PIDs fighting over the same uinput. ptt_pynput got nothing.
#
# New rule: at the top of every load() call, kill any rogue antimicrox that
# isn't tracked in our pidfile. Then check the tracked one is still alive and
# its profile path matches what we want to load.
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

# Kill ANY antimicrox not tracked in our pidfile. Safe to call on every load().
kill_rogue_antimicrox() {
    local tracked_pid=""
    [[ -s "$ANTIMICROX_PIDFILE" ]] && tracked_pid=$(cat "$ANTIMICROX_PIDFILE" 2>/dev/null)
    local rogue_pids=()
    for pid in $(pgrep -x antimicrox 2>/dev/null); do
        if [[ "$pid" != "$tracked_pid" ]]; then
            rogue_pids+=("$pid")
        fi
    done
    if (( ${#rogue_pids[@]} > 0 )); then
        echo "$(date '+%H:%M:%S') → killing ${#rogue_pids[@]} rogue antimicrox: ${rogue_pids[*]}"
        for pid in "${rogue_pids[@]}"; do
            kill -TERM "$pid" 2>/dev/null || true
        done
        # wait for them to die (don't pkill — that would nuke the tracked one too)
        for _ in $(seq 1 20); do
            local still_alive=0
            for pid in "${rogue_pids[@]}"; do
                kill -0 "$pid" 2>/dev/null && still_alive=1
            done
            [[ $still_alive -eq 0 ]] && break
            sleep 0.2
        done
        for pid in "${rogue_pids[@]}"; do
            kill -KILL "$pid" 2>/dev/null || true
        done
    fi
}

load() {
    local profile="$1" label="$2"
    # DEDUP: always sweep rogue antimicrox (those not in our pidfile) before
    # deciding whether to load. This is the fix for the duplicate-instance bug
    # where xone reload re-enumerated the controller and spawned a second one.
    kill_rogue_antimicrox
    # If antimicrox died externally, reset so we restart even if profile path unchanged
    if [[ "$profile" == "$current_profile" ]] && ! antimicrox_alive; then
        current_profile=""
    fi
    [[ "$profile" == "$current_profile" ]] && antimicrox_alive && return
    kill_antimicrox
    kill_rogue_antimicrox
    rm -f "$ANTIMICROX_PIDFILE"
    setsid nohup "$ANTIMICROX" --profile "$profile" --tray --eventgen uinput > /tmp/antimicrox.log 2>&1 &
    local loader=$!
    echo "$loader" > "$ANTIMICROX_PIDFILE"
    current_profile="$profile"
    echo "$label" > ~/.controller_current_profile
    DISPLAY=:0 DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${UID}/bus" \
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

LOCK_FILE="${HOME}/.config/ai-controller/lock_desktop_profile"

while true; do
    if controller_present; then
        if [[ -f "$LOCK_FILE" ]]; then
            # Launcher/user has requested the desktop profile stay loaded no
            # matter which window is focused.
            load "$DESKTOP_PROFILE" "Desktop (locked)"
        else
            # Default to the single general desktop profile, but switch to a
            # specialized layout when YouTube TV is in focus.
            title=$(active_window_title)
            if is_youtube_tv "$title"; then
                load "$YOUTUBE_TV_PROFILE" "YouTube TV"
            else
                load "$DESKTOP_PROFILE" "Desktop"
            fi
        fi
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
