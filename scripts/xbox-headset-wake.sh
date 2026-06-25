#!/bin/bash
# xbox-headset-wake.sh
# ---------------------------------------------------------------------------
# Auto-wake the Xbox controller headset mic on connect so the user never has
# to physically pull and reinsert the 3.5mm plug.
#
# ROOT CAUSE: the controller announces its headset over the GIP protocol only
# on an analog insertion edge. When it powers on / reconnects with the plug
# already seated, no edge fires, so xone never creates the capture device.
# A physical reseat creates the edge. This script reproduces that edge in
# software via escalating "kicks", triggered by udev on controller connect.
#
# Triggered by: /etc/udev/rules.d/52-xbox-headset-wake.rules
#               -> systemctl --no-block start xbox-headset-wake.service
# Safe: if the headset is already present it does nothing. Loop-guarded so the
# USB re-auth kick cannot retrigger itself endlessly.
# ---------------------------------------------------------------------------
set -u

VENDOR="045e"
PRODUCT="0b12"
MODULE="xone_gip_headset"
LOCK="/run/xbox-headset-wake.lock"
TAG="xbox-headset-wake"

log() { logger -t "$TAG" -- "$1"; }

headset_present() { grep -q "Xbox Headset" /proc/asound/cards 2>/dev/null; }

# ── loop guard: don't re-run within 30s (USB re-auth retriggers udev 'add') ──
now=$(date +%s)
if [ -f "$LOCK" ]; then
    last=$(stat -c %Y "$LOCK" 2>/dev/null || echo 0)
    if [ $(( now - last )) -lt 30 ]; then
        exit 0
    fi
fi
touch "$LOCK"

# ── let the GIP stack settle after connect ──────────────────────────────────
sleep 3

if headset_present; then
    log "headset present on connect — no wake needed"
    exit 0
fi

log "headset absent after connect — attempting software reseat"

# Locate the controller's USB device node (e.g. /sys/bus/usb/devices/1-4)
DEVPATH=""
for d in /sys/bus/usb/devices/*; do
    [ -f "$d/idVendor" ] || continue
    if [ "$(cat "$d/idVendor" 2>/dev/null)" = "$VENDOR" ] && \
       [ "$(cat "$d/idProduct" 2>/dev/null)" = "$PRODUCT" ]; then
        DEVPATH="$d"
        break
    fi
done

# ── Kick 1: reload the GIP headset client driver (cheap) ────────────────────
modprobe -r "$MODULE" 2>/dev/null
modprobe "$MODULE" 2>/dev/null
sleep 2
if headset_present; then
    log "headset woke after GIP headset module reload"
    exit 0
fi

# ── Kick 2: software re-enumerate the controller (closest to a real reseat) ─
if [ -n "$DEVPATH" ] && [ -w "$DEVPATH/authorized" ]; then
    echo 0 > "$DEVPATH/authorized" 2>/dev/null
    sleep 1
    echo 1 > "$DEVPATH/authorized" 2>/dev/null
    sleep 3
    if headset_present; then
        log "headset woke after USB re-authorize ($DEVPATH)"
        exit 0
    fi
fi

log "headset still absent after wake attempts — physical 3.5mm reseat may be required"
exit 0
