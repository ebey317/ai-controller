#!/usr/bin/env bash
# gip-audio-watchdog: auto-recover from the xone-gip USB audio buffer wedge.
# Symptom: "gip_send_audio_samples: get buffer failed" flooding the kernel
# log — heard as radio static on the controller headset and corrupted STT.
# Recovery: reset-controller-audio.sh (profile replug cycle).
#
# Policy: check every CHECK_INTERVAL seconds; if more than THRESHOLD
# failures appeared in the last window, run the shallow (sound-server) reset.
# If the wedge comes back after a shallow reset already ran recently, the
# problem is kernel-side (ENOSPC iso bandwidth leak) — escalate to the deep
# reset (module reload) via the NOPASSWD sudoers rule, if installed.
# COOLDOWN prevents a replug loop if neither reset clears the wedge.

CHECK_INTERVAL=60
THRESHOLD=5
COOLDOWN=300
ESCALATE_WINDOW=900   # shallow reset that recently "worked" + wedge back = go deep
RESET_SCRIPT="$HOME/ai-controller/scripts/reset-controller-audio.sh"
DEEP_RESET="/usr/local/bin/gip-deep-reset.sh"
LOG="$HOME/ai-controller/logs/gip-watchdog.log"

mkdir -p "$(dirname "$LOG")"
last_reset=0
last_shallow=0

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

log "watchdog started (threshold=$THRESHOLD/${CHECK_INTERVAL}s, cooldown=${COOLDOWN}s)"

while true; do
    sleep "$CHECK_INTERVAL"

    count=$(journalctl -k --since "$CHECK_INTERVAL sec ago" 2>/dev/null \
        | grep -c "gip_send_audio_samples: get buffer failed")

    if [ "$count" -gt "$THRESHOLD" ]; then
        now=$(date +%s)
        if [ $((now - last_reset)) -lt "$COOLDOWN" ]; then
            log "wedge active ($count failures) but in cooldown — skipping reset"
            continue
        fi
        # Shallow reset already ran recently and the wedge is back → the
        # wedge is kernel-side; the sound-server reset can't hold it.
        if [ "$last_shallow" -gt 0 ] && [ $((now - last_shallow)) -lt "$ESCALATE_WINDOW" ] \
           && [ -x "$DEEP_RESET" ] && sudo -n -l "$DEEP_RESET" >/dev/null 2>&1; then
            log "wedge back within ${ESCALATE_WINDOW}s of shallow reset ($count failures) — escalating to deep reset (module reload)"
            if sudo -n "$DEEP_RESET" >> "$LOG" 2>&1; then
                last_reset=$now
                last_shallow=0
                log "deep reset completed"
            else
                last_reset=$now
                log "deep reset FAILED — sudoers rule missing? run install-gip-deep-reset.sh"
            fi
            continue
        fi

        log "wedge detected: $count failures in last ${CHECK_INTERVAL}s — running shallow reset"
        if "$RESET_SCRIPT" >> "$LOG" 2>&1; then
            last_reset=$now
            last_shallow=$now
            log "shallow reset completed"
        else
            last_reset=$now
            log "shallow reset FAILED (exit $?) — will retry after cooldown"
        fi
    fi
done
