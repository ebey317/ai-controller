#!/usr/bin/env bash
# controller-detect.sh — Auto-detect controller and load correct profile
# Runs via udev / systemd on controller plug-in
# Triggered by: antimicrox-autoload.service

ANTIMICROX="${HOME}/scripts/antimicrox.AppImage"
PROFILE_DIR="${HOME}/.config/antimicrox"

# Known vendor IDs
XBOX_VENDOR="045e"     # Microsoft Xbox
PS_VENDOR="054c"       # Sony PlayStation
BROOK_VENDOR="0c12"    # Brook universal adapter (common for legacy controllers)

# Find connected gamepads
detect_controller() {
    local vendor=""
    for device in /sys/class/input/js*; do
        if [[ -d "$device" ]]; then
            local uevent="$device/../../../uevent"
            if [[ -f "$uevent" ]]; then
                vendor=$(grep -i "^ID_VENDOR_ID\|^HID_ID" "$uevent" 2>/dev/null | head -1 | grep -oP '[0-9a-fA-F]{4}' | head -1)
                break
            fi
        fi
    done
    echo "${vendor,,}"
}

# Load profile based on vendor
load_profile() {
    local vendor="$1"
    local profile=""

    case "$vendor" in
        "$XBOX_VENDOR")
            profile="$PROFILE_DIR/ai-desktop.amgp"
            echo "Xbox controller detected → loading desktop profile"
            ;;
        "$PS_VENDOR")
            profile="$PROFILE_DIR/ai-desktop.amgp"
            echo "PlayStation controller detected → loading desktop profile"
            ;;
        "$BROOK_VENDOR")
            profile="$PROFILE_DIR/ai-desktop.amgp"
            echo "Brook adapter detected → loading desktop profile"
            ;;
        *)
            profile="$PROFILE_DIR/ai-desktop.amgp"
            echo "Generic controller (vendor: $vendor) → loading desktop profile"
            ;;
    esac

    # Kill existing antimicroX instance
    pkill -f antimicrox 2>/dev/null || true
    sleep 1

    # Ensure F13 has an X11 keycode (not in default keymap — needed for push-to-talk)
    DISPLAY="${DISPLAY:-:0}" xmodmap -e "keycode 202 = F13" 2>/dev/null || true

    # Launch with detected profile (headless daemon mode)
    if [[ -f "$ANTIMICROX" ]]; then
        DISPLAY="${DISPLAY:-:0}" nohup "$ANTIMICROX" --profile "$profile" --tray > /tmp/antimicrox.log 2>&1 &
        echo "antimicroX launched with profile: $profile"
    else
        echo "ERROR: antimicroX not found at $ANTIMICROX"
        echo "Run: bash install.sh"
        exit 1
    fi
}

# Watch for controller events
watch_mode() {
    echo "Watching for controller connections..."
    while true; do
        if ls /dev/input/js* &>/dev/null 2>&1; then
            vendor=$(detect_controller)
            load_profile "$vendor"
            # Wait until controller disconnects
            while ls /dev/input/js* &>/dev/null 2>&1; do
                sleep 5
            done
            echo "Controller disconnected — stopping antimicroX"
            pkill -f antimicrox 2>/dev/null || true
        fi
        sleep 2
    done
}

# One-shot mode (udev trigger)
if [[ "$1" == "--watch" ]]; then
    watch_mode
else
    vendor=$(detect_controller)
    if [[ -n "$vendor" ]]; then
        load_profile "$vendor"
    else
        echo "No controller detected. Plug in controller and re-run."
    fi
fi
