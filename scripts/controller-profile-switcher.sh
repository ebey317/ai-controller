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
# ACTIVE PROFILE: <PROFILE_DIR>/dont delete .gamecontroller.amgp
# This is the ONLY profile in use - all modes use the same general-purpose layout.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$(dirname "$SCRIPT_DIR")"

# Prefer a pre-extracted AppImage (plain files on disk, no FUSE mount to
# lose mid-run) over the raw .AppImage. Running the .AppImage directly keeps
# it FUSE-mounted for the process lifetime; under rapid respawns that mount
# has been observed to vanish while AntiMicroX is still reading pages from
# it, producing a SIGBUS ("Can't open file .../libQt5Gui.so.5 during
# file-backed mapping note processing" per coredumpctl gdb). Extracting once
# removes FUSE from the picture entirely.
EXTRACTED_ANTIMICROX="${HOME}/scripts/antimicrox-extracted/squashfs-root/AppRun"
if [[ -x "$EXTRACTED_ANTIMICROX" ]]; then
    ANTIMICROX="$EXTRACTED_ANTIMICROX"
elif command -v antimicrox &>/dev/null; then
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
if [ -d "${HOME}/.config/antimicrox" ] && [ -f "${HOME}/.config/antimicrox/dont delete .gamecontroller.amgp" ]; then
    PROFILE_DIR="${HOME}/.config/antimicrox"
else
    PROFILE_DIR="${INSTALL_DIR}/profiles"
    # Warn if the repo profile still has the unresolved placeholder (install.sh not run)
    if grep -q '__AI_CONTROLLER_DIR__' "${PROFILE_DIR}/dont delete .gamecontroller.amgp" 2>/dev/null; then
        echo "WARNING: Profile has unresolved __AI_CONTROLLER_DIR__ placeholder." >&2
        echo "Run install.sh to resolve paths, or the slide keyboard will not work." >&2
    fi
fi
export DISPLAY="${DISPLAY:-:0}"

# Stabilize Qt / AntiMicroX: disable network bearer polling (prevent SIGBUS)
export QT_BEARER_POLL_TIMEOUT=-1
export QT_NO_NETWORK_PROBING=1

# NOTE: removed SDL_JOYSTICK_DEVICE override. It forces js0 globally and
# can confuse AntiMicroX when the real mouse or other devices are present.

PROFILE="${PROFILE_DIR}/dont delete .gamecontroller.amgp"
DESKTOP_PROFILE="$PROFILE"
BROWSER_PROFILE="$PROFILE"
YOUTUBE_TV_PROFILE="$PROFILE"
IPTV_PROFILE="$PROFILE"

# Ensure F13-F18 have X11 keycodes (not in default keymap).
# Needed by ptt_pynput.py dictation and onboard keyboard scanner.
# A desktop keymap reload (which a USB controller hotplug can trigger) silently
# wipes this overlay, so it must be re-applied on every reconnect, not just once
# at script start — see watch_controller()'s "present" branch below.
ensure_f13_keymap() {
    DISPLAY=:0 xmodmap -e "keycode 191 = F13" 2>/dev/null || true
    DISPLAY=:0 xmodmap -e "keycode 202 = F13" 2>/dev/null || true
    DISPLAY=:0 xmodmap -e "keycode 197 = F14" 2>/dev/null || true
    DISPLAY=:0 xmodmap -e "keycode 217 = F15" 2>/dev/null || true
    DISPLAY=:0 xmodmap -e "keycode 219 = F16" 2>/dev/null || true
    DISPLAY=:0 xmodmap -e "keycode 222 = F17" 2>/dev/null || true
    DISPLAY=:0 xmodmap -e "keycode 230 = F18" 2>/dev/null || true
}
ensure_f13_keymap

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
#
# PGREP_PATTERN_FIX (2026-07-16): `pkill -x antimicrox` / `pgrep -x antimicrox`
# only ever match a process whose exact comm is "antimicrox". The AppImage
# path used here never produces that: it runs as "AppRun.wrapped" and
# "antimicrox.AppI" (truncated comm). Exact-name matching silently matched
# nothing, so this was a no-op — the tracked instance was never actually
# killed, and antimicrox_alive() below always reported "dead" (see its own
# comment), causing a new instance to be spawned on top of the old one every
# ~2-3s. Fixed to match by command-line pattern (catches both process names)
# instead of exact comm name.
ANTIMICROX_PROC_PATTERN='antimicrox\.AppImage|AppRun\.wrapped.*gamecontroller|/antimicrox($| )'
kill_antimicrox() {
    pkill -f "$ANTIMICROX_PROC_PATTERN" 2>/dev/null
    for _ in $(seq 1 30); do
        pgrep -f "$ANTIMICROX_PROC_PATTERN" > /dev/null 2>&1 || break
        sleep 0.2
    done
    release_stuck_keys
    sleep 0.5
}

# antimicrox owns the uinput virtual keyboard that emits F13-F18 (dictation
# triggers) and modifier-combined codes for other buttons. If it's killed
# while one of those is physically/logically held, X11 never receives the
# matching key-up and treats that key as permanently down — the next real
# keystroke then reads as a stuck-modifier combo (e.g. phantom Ctrl turns
# the next letter into Ctrl+C) and kills whatever's running in the
# foreground terminal. xdotool keyup issues a synthetic XTestFakeKeyEvent
# release at the X server level, independent of which device asserted the
# key, so this resets state even after the asserting device is gone.
release_stuck_keys() {
    for key in F13 F14 F15 F16 F17 F18 \
               Control_L Control_R Shift_L Shift_R Alt_L Alt_R Super_L Super_R; do
        DISPLAY=:0 xdotool keyup "$key" 2>/dev/null || true
    done
}

controller_present() {
    # Not hardcoded to js0: a GIP reconnect can renumber the joystick node
    # (js1, js2, ...) if the old one wasn't fully torn down. Match any.
    compgen -G "/dev/input/js*" > /dev/null
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
                ensure_f13_keymap
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

# xone reconnect watcher — journalctl over 48h showed 108 full GIP
# disconnect/reconnect handshakes, most of which don't remove
# /dev/input/js*, so watch_controller()'s device-node poll above misses
# them entirely. Tailing the kernel's own disconnect/reconnect messages
# catches every one directly: release_stuck_keys means a drop mid-hold can
# never leave a phantom modifier, and notify-send means the user gets a
# real-time "that's why it just didn't register" instead of a silent gap.
# This does not fix the underlying link instability -- that's the xone
# driver / wireless dongle, out of this script's reach -- it only makes the
# drops visible and cleans up after them immediately instead of leaving
# stale key/button state around.
watch_xone_reconnect() {
    # NOT `journalctl -k`: confirmed empirically on this machine that -k
    # silently excludes these exact lines (0 matches) even though their own
    # metadata says _TRANSPORT=kernel SYSLOG_FACILITY=0 -- an unexplained
    # local quirk, not worth chasing further. Plain -f with a distinctive
    # substring match is what actually sees them (confirmed: 25/25 present).
    journalctl -f -n0 --no-pager 2>/dev/null | while read -r line; do
        case "$line" in
            *"gip_handle_pkt_status: disconnected"*)
                echo "$(date '+%H:%M:%S') → xone: controller link dropped"
                release_stuck_keys
                DISPLAY=:0 DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${UID}/bus" \
                    notify-send --replace-id=7002 -t 2500 -u low \
                    "🎮 AI Controller" "Controller link dropped — reconnecting…" 2>/dev/null || true
                ;;
            *"gip_handle_pkt_announce: address="*)
                echo "$(date '+%H:%M:%S') → xone: controller reconnected"
                release_stuck_keys
                ensure_f13_keymap
                DISPLAY=:0 DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${UID}/bus" \
                    notify-send --replace-id=7002 -t 2000 -u low \
                    "🎮 AI Controller" "Controller reconnected." 2>/dev/null || true
                ;;
        esac
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
        # See PGREP_PATTERN_FIX above: was `pgrep -x antimicrox`, which never
        # matched the AppImage's real process names and made this always
        # report "dead" even with a healthy instance running.
        if kill -0 "$pid" 2>/dev/null && pgrep -f "$ANTIMICROX_PROC_PATTERN" > /dev/null 2>&1; then
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
    # tracked_pid is the setsid session leader for our launch, so anything
    # sharing its session is "ours," not rogue — compare by session id, not
    # raw pid, since the AppImage spawns child processes with different pids
    # in the same session (see PGREP_PATTERN_FIX above for why exact-name
    # pgrep/pkill never worked here at all).
    local tracked_sid=""
    [[ -n "$tracked_pid" ]] && tracked_sid=$(ps -o sid= -p "$tracked_pid" 2>/dev/null | tr -d ' ')
    local rogue_pids=()
    for pid in $(pgrep -f "$ANTIMICROX_PROC_PATTERN" 2>/dev/null); do
        local pid_sid
        pid_sid=$(ps -o sid= -p "$pid" 2>/dev/null | tr -d ' ')
        if [[ -z "$tracked_sid" ]] || [[ "$pid_sid" != "$tracked_sid" ]]; then
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
    # Prevent debug-spam log from eating all disk/RAM: rotate/append through a
    # small named pipe + logger so the file can't grow unbounded.  The old
    # redirect to /tmp/antimicrox.log produced a 4.6 GB debug leak after ~12 h.
    setsid nohup bash -c "exec 1> >(exec logger -t antimicrox -p user.info); exec 2>&1; \
        \"$ANTIMICROX\" --profile \"$profile\" --tray --eventgen uinput" &
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
# watch_xone_reconnect is a pipeline (journalctl | while read), so $! only
# captures the `while read` end -- journalctl itself would be orphaned by a
# plain `kill $XONE_WATCH_PID` and pile up across every restart of this
# service. Sweep by pattern instead, same idiom as ANTIMICROX_PROC_PATTERN.
pkill -f "journalctl -f -n0" 2>/dev/null || true
watch_xone_reconnect &
XONE_WATCH_PID=$!
trap 'kill $WATCH_PID $XONE_WATCH_PID 2>/dev/null; pkill -f "journalctl -f -n0" 2>/dev/null; rm -f /tmp/controller_state_changed; kill_antimicrox; exit 0' EXIT INT TERM

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
