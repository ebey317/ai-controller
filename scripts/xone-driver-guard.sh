#!/usr/bin/env bash
# AI Controller — xone driver guard
# Ensures Xbox Series X/S controllers (045e:0b12) are bound ONLY by xone,
# never by the in-kernel xpad driver. Run at boot, on udev connect, or manually.
#
# Design goals:
#   - Idempotent and quiet when already correct.
#   - Unloads xpad if it somehow loaded.
#   - Loads xone-wired and xone-gip-headset when the controller is present.
#   - Restarts the AI Controller user services only if it had to correct the driver.
#
# Run as root (systemd service) or with passwordless sudo (launcher fallback).
set -uo pipefail

TAG="xone-driver-guard"
CONTROLLER_USB_ID="045e:0b12"
XONE_MODULES=(xone-wired xone-gip-headset)

log() { logger -t "$TAG" -- "$1"; }

# ── helpers ─────────────────────────────────────────────────────────────────

controller_present() {
    lsusb -d "$CONTROLLER_USB_ID" >/dev/null 2>&1
}

xpad_loaded() { lsmod | grep -q '^xpad '; }
xone_wired_loaded() { lsmod | grep -q '^xone_wired '; }

reload_xone() {
    log "loading xone modules"
    for mod in "${XONE_MODULES[@]}"; do
        modprobe "$mod" 2>/dev/null || true
    done
}

unload_xpad() {
    log "xpad present — unloading"
    modprobe -r xpad 2>/dev/null || rmmod xpad 2>/dev/null || true
}

restart_user_services() {
    # Restart only if a desktop user session is available.
    local uid
    for uid in /run/user/*; do
        [ -d "$uid" ] || continue
        local uid_num
        uid_num=$(basename "$uid")
        local username
        username=$(getent passwd "$uid_num" | cut -d: -f1)
        [ -n "$username" ] || continue
        log "restarting AI Controller services for $username"
        runuser -u "$username" -- systemctl --user restart \
            antimicrox-autoload.service \
            voice-bridge.service \
            ptt-pynput.service \
            controller-legend.service \
            ai-slide-keyboard.service \
            2>/dev/null || true
    done
}

# ── main guard ──────────────────────────────────────────────────────────────

main() {
    local changed=0

    if ! controller_present; then
        log "controller not present — nothing to do"
        exit 0
    fi

    if xpad_loaded; then
        unload_xpad
        changed=1
    fi

    if ! xone_wired_loaded; then
        reload_xone
        changed=1
    fi

    # Final verification
    if ! xone_wired_loaded; then
        log "FAILED: xone-wired did not load after correction"
        exit 1
    fi

    if xpad_loaded; then
        log "FAILED: xpad still loaded after correction"
        exit 1
    fi

    if [ "$changed" -eq 1 ]; then
        log "xone-only driver state enforced"
        # Give the driver a moment to settle before the user services attach.
        sleep 1
        restart_user_services
    else
        log "xone-only driver state already correct"
    fi

    exit 0
}

main "$@"
