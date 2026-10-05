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
#
# Slow-drip rule (LONG_*): the two rules above both need failures close
# together — a burst inside one window, or DRIP_STREAK consecutive windows.
# A wedge spaced further apart than DRIP_STREAK*CHECK_INTERVAL can never
# satisfy either, and it is no less audible for being slow. 2026-10-01: 40
# events over 6.7h at a 170s minimum gap fired these rules ZERO times, so
# the watchdog logged nothing for 3 days while static accumulated. Counting
# failures over a long window catches that pattern without reacting to a
# single blip.

CHECK_INTERVAL=20
THRESHOLD=5
DRIP_STREAK=3         # consecutive windows with >=1 failure = chronic low-rate wedge
COOLDOWN=300
ESCALATE_WINDOW=900   # shallow reset that recently "worked" + wedge back = go deep
LONG_WINDOW=1200      # slow-drip look-back window (seconds)
LONG_THRESHOLD=3      # failures inside LONG_WINDOW that count as a wedge
RESET_SCRIPT="$HOME/ai-controller/scripts/reset-controller-audio.sh"
DEEP_RESET="/usr/local/bin/gip-deep-reset.sh"
LOG="$HOME/ai-controller/logs/gip-watchdog.log"

mkdir -p "$(dirname "$LOG")"
last_reset=0
last_shallow=0
drip_count=0
long_hits=()          # epochs of windows that saw >=1 failure, inside LONG_WINDOW

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

log "watchdog started (threshold=$THRESHOLD/${CHECK_INTERVAL}s, drip_streak=${DRIP_STREAK}, long=${LONG_THRESHOLD}/${LONG_WINDOW}s, cooldown=${COOLDOWN}s)"

while true; do
    sleep "$CHECK_INTERVAL"

    count=$(journalctl -k --since "$CHECK_INTERVAL sec ago" 2>/dev/null \
        | grep -c "gip_send_audio_samples: get buffer failed")

    now=$(date +%s)

    # Long-window bucket: one epoch per window that saw a failure, pruned to
    # LONG_WINDOW. Pruning on every pass keeps an old burst from lingering and
    # re-triggering after a successful recovery.
    if [ "$count" -ge 1 ]; then
        long_hits+=("$now")
    fi
    if [ "${#long_hits[@]}" -gt 0 ]; then
        _cut=$((now - LONG_WINDOW))
        _keep=()
        for _h in "${long_hits[@]}"; do
            if [ "$_h" -ge "$_cut" ]; then
                _keep+=("$_h")
            fi
        done
        long_hits=("${_keep[@]}")
    fi

    # A steady ~1/window drip never crosses THRESHOLD in any single window
    # but is exactly as audible (one click per window, indefinitely) as a
    # burst is -- confirmed 2026-08-19: hours of continuous single-digit
    # failures per minute that the burst-only check left completely
    # unhandled. Track a consecutive-window streak so sustained low-rate
    # wedging escalates the same as a burst, without reacting to one
    # isolated blip (streak resets to 0 the moment a window comes back clean).
    if [ "$count" -ge 1 ]; then
        drip_count=$((drip_count + 1))
    else
        drip_count=0
    fi

    # Which rule fired, in words. The slow-drip rule is the one that catches a
    # wedge spaced further apart than DRIP_STREAK*CHECK_INTERVAL -- here
    # anything past ~60s -- which is what 2026-10-01 looked like.
    reason=""
    if [ "$count" -gt "$THRESHOLD" ]; then
        reason="burst: $count failures in last ${CHECK_INTERVAL}s"
    elif [ "$drip_count" -ge "$DRIP_STREAK" ]; then
        reason="chronic drip: $count failures/window for $drip_count consecutive windows"
    elif [ "${#long_hits[@]}" -ge "$LONG_THRESHOLD" ]; then
        reason="slow drip: ${#long_hits[@]} failing windows in last ${LONG_WINDOW}s"
    fi

    if [ -n "$reason" ]; then
        drip_count=0
        long_hits=()
        if [ $((now - last_reset)) -lt "$COOLDOWN" ]; then
            log "$reason but in cooldown — skipping reset"
            continue
        fi
        # Shallow reset already ran recently and the wedge is back → the
        # wedge is kernel-side; the sound-server reset can't hold it.
        if [ "$last_shallow" -gt 0 ] && [ $((now - last_shallow)) -lt "$ESCALATE_WINDOW" ] \
           && [ -x "$DEEP_RESET" ] && sudo -n -l "$DEEP_RESET" >/dev/null 2>&1; then
            log "$reason — wedge back within ${ESCALATE_WINDOW}s of shallow reset, escalating to deep reset (module reload)"
            # shellcheck disable=SC2024  # $LOG lives under $HOME and is
            # user-writable, so the redirect is opened by the invoking user
            # deliberately. Switching to `sudo tee` would replace the reset
            # command's exit status with tee's and make the else branch below
            # unreachable — it would report success on a failed reset.
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

        log "$reason — running shallow reset"
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
