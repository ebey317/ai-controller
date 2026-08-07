#!/usr/bin/env bash
# gip-deep-reset: kernel-level recovery for the xone-gip headset audio wedge.
# Use when reset-controller-audio.sh (sound-server level) doesn't hold —
# i.e. "get buffer failed: -28" (ENOSPC) keeps returning, heard as radio
# static. Reloading xone_gip_headset frees the driver's wedged isochronous
# USB bandwidth reservations and re-registers the audio device clean.
#
# Touches ONLY the headset client module — gamepad input (xone_gip_gamepad /
# xone_wired) stays loaded, so the controller trigger keeps working.
#
# Must run as root (via the NOPASSWD sudoers rule installed alongside it).
set -uo pipefail

TAG="gip-deep-reset"
log() { logger -t "$TAG" -- "$1"; echo "$TAG: $1"; }

if [ "$(id -u)" -ne 0 ]; then
    echo "$TAG: must run as root (sudo)" >&2
    exit 1
fi

log "reloading xone_gip_headset module"
modprobe -r xone_gip_headset 2>/dev/null || rmmod xone_gip_headset 2>/dev/null
sleep 1
modprobe xone_gip_headset || { log "FAILED to reload xone_gip_headset"; exit 1; }

# Give the headset time to re-register and the sound card to reappear.
for _ in $(seq 1 10); do
    lsmod | grep -q '^xone_gip_headset' && break
    sleep 0.5
done

# Re-apply the audio profile as the desktop user so PipeWire picks the
# fresh card up with the correct output+input profile.
for uid in /run/user/*; do
    [ -d "$uid" ] || continue
    uid_num=$(basename "$uid")
    username=$(getent passwd "$uid_num" | cut -d: -f1)
    [ -n "$username" ] || continue
    sleep 2
    runuser -u "$username" -- bash "/home/$username/ai-controller/scripts/reset-controller-audio.sh" 2>/dev/null || true
done

log "done"
exit 0
