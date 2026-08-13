#!/bin/bash
# Stop the full AI Controller stack — the missing counterpart to start-all.sh.
#
# WHY THIS SCRIPT EXISTS
# ----------------------
# Three separate things make this stack refuse to die, and a naive
# `pkill -f ptt_pynput` hits all three:
#
#   1. Restart= policies fight you. antimicrox-autoload is Restart=always
#      (back in 2s). Everything else is Restart=on-failure — and systemd
#      counts *death by signal* as a failure. So pkill GUARANTEES a respawn.
#      Only `systemctl --user stop` is a clean exit. Services first, always.
#
#   2. `sg input -c` in ptt-pynput's ExecStart runs a root-owned process
#      inside a user-manager cgroup. The user systemd (systemd[1018]) is not
#      permitted to SIGKILL it, so it survives `stop` and logs
#      "Unit process N remains running after unit stopped."
#
#   3. Hand-launched copies escape the cgroup entirely. A listener started
#      from a terminal (`bash -lic 'set +m; ... sg input -c ...'`) lands in
#      a vte-spawn scope, not the service cgroup. systemctl cannot see it,
#      cannot stop it, and it keeps reading the controller forever.
#
# So: stop the units, THEN reap whatever escaped. Order matters.
set -uo pipefail

SELF_PID=$$
DRY_RUN="${DRY_RUN:-0}"

# Reverse of start-all.sh order: UI and listeners first, backend last, so
# nothing is left POSTing to a dead :8002 mid-shutdown.
SERVICES=(
    ai-slide-keyboard.service
    controller-legend.service
    ptt-pynput.service
    antimicrox-autoload.service
    voice-bridge.service
)

# Processes that belong to this stack, matched by command line. Used ONLY to
# find strays that escaped systemd — units are stopped by name, above.
STRAY_PATTERNS=(
    'scripts/ptt_pynput\.py'
    'scripts/voice_bridge\.py'
    'scripts/controller-legend\.py'
    'scripts/slide_keyboard\.py'
    'scripts/controller-profile-switcher\.sh'
)

log() { printf '  %s\n' "$*"; }

# Speak through Hermes TTS. Two rules make this safe inside a kill button:
#
#   1. Phrases are warmed into the cache BEFORE anything is torn down, so the
#      announcement never needs network or a live service at kill time.
#   2. It never blocks and never fails the stop. A silent kill button that
#      worked beats a chatty one that hung waiting on audio.
SAY_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/tts_say.sh"
SPEAK="${SPEAK:-1}"

say() {
    [[ "$SPEAK" != "1" ]] && return 0
    [[ ! -x "$SAY_SCRIPT" ]] && return 0
    ( timeout 15 "$SAY_SCRIPT" "$1" >/dev/null 2>&1 & ) 2>/dev/null
    return 0
}

# Synthesize every phrase we might need while the stack is still healthy.
warm_phrases() {
    [[ "$SPEAK" != "1" || ! -x "$SAY_SCRIPT" ]] && return 0
    local p
    for p in "$PHRASE_STOPPING" "$PHRASE_DONE" "$PHRASE_PARTIAL"; do
        timeout 20 "$SAY_SCRIPT" --warm "$p" >/dev/null 2>&1
    done
    return 0
}

PHRASE_STOPPING="Stopping AI controller."
PHRASE_DONE="AI controller stopped."
PHRASE_PARTIAL="AI controller partially stopped. Check the terminal."

# ---------------------------------------------------------------------------
# Phase 1 — stop the supervised units. This is the ONLY clean exit path;
# it is the one route that does not trip Restart=.
# ---------------------------------------------------------------------------
stop_services() {
    echo "[1/3] Stopping systemd units..."
    for unit in "${SERVICES[@]}"; do
        if [[ "$DRY_RUN" == "1" ]]; then
            log "DRY-RUN would stop: $unit"
            continue
        fi
        systemctl --user stop "$unit" 2>/dev/null
        local state
        state="$(systemctl --user is-active "$unit" 2>/dev/null)"
        log "$(printf '%-32s %s' "$unit" "$state")"
    done
}

# ---------------------------------------------------------------------------
# Phase 2 — find survivors.
#
# A PID is a "stray" if it matches one of our patterns but is NOT inside a
# cgroup belonging to one of our (now-stopped) services. Those are the ghosts
# from cause #2 and #3 above.
#
# NOTE the pgrep self-match footgun: this script's own command line contains
# the pattern strings, so we always exclude $SELF_PID and our own children.
# ---------------------------------------------------------------------------
find_strays() {
    local pattern pid cgroup
    for pattern in "${STRAY_PATTERNS[@]}"; do
        while read -r pid; do
            [[ -z "$pid" ]] && continue
            [[ "$pid" == "$SELF_PID" ]] && continue
            [[ ! -d "/proc/$pid" ]] && continue
            cgroup="$(tail -1 "/proc/$pid/cgroup" 2>/dev/null)"
            printf '%s\t%s\t%s\n' "$pid" "$cgroup" \
                "$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | cut -c1-90)"
        done < <(pgrep -f "$pattern" 2>/dev/null)
    done | sort -u -k1,1n
}

# Decide whether a surviving process is safe to kill.
#
# Policy, most conservative constraint first:
#
#   GATE — it must provably belong to THIS install. There are two checkouts
#   on this box (~/ai-controller and ~/projects/ai-controller); only the one
#   named by AI_CONTROLLER_DIR is ours. A stray voice_bridge.py from the
#   other tree is somebody else's problem and stays out of the blast radius.
#
#   Then reap on either of two proofs that it is genuinely orphaned:
#     a) still in a *.service cgroup AFTER phase 1 — it was ordered to die
#        and refused. This is the root-owned `sg input` case.
#     b) in a terminal scope (vte-spawn/session/app-*Terminal*) — hand-
#        launched with `set +m`, so systemd never had authority over it.
#        These are the ghosts that keep eating controller input.
#
# Anything else gets reported, not killed.
INSTALL_DIR="${AI_CONTROLLER_DIR:-$HOME/ai-controller}"

should_reap() {
    local pid="$1" cgroup="$2" cmd="$3"

    # Gate: must be from our install.
    [[ "$cmd" != *"$INSTALL_DIR/"* ]] && return 1

    # (a) survived a direct systemd stop
    [[ "$cgroup" == *.service ]] && return 0

    # (b) escaped into a terminal scope
    [[ "$cgroup" == *vte-spawn* ]] && return 0
    [[ "$cgroup" == *Terminal* ]] && return 0
    [[ "$cgroup" == *session-*.scope ]] && return 0

    return 1
}

reap_strays() {
    echo "[2/3] Checking for processes that escaped systemd..."
    local found=0 reaped=0
    while IFS=$'\t' read -r pid cgroup cmd; do
        [[ -z "$pid" ]] && continue
        found=$((found + 1))
        if should_reap "$pid" "$cgroup" "$cmd"; then
            if [[ "$DRY_RUN" == "1" ]]; then
                log "DRY-RUN would reap PID $pid  ($cmd)"
            else
                # SIGTERM the leaf first; a root-owned `sg` wrapper exits on
                # its own once its child is gone, so we rarely need sudo.
                kill -TERM "$pid" 2>/dev/null
                sleep 0.3
                kill -KILL "$pid" 2>/dev/null
                log "reaped PID $pid  ($cmd)"
            fi
            reaped=$((reaped + 1))
        else
            log "LEFT RUNNING PID $pid  [$cgroup]"
            log "                  $cmd"
        fi
    done < <(find_strays)

    if [[ "$found" -eq 0 ]]; then
        log "none — clean shutdown, nothing escaped."
    else
        log "$found survivor(s), $reaped reaped."
    fi
}

# ---------------------------------------------------------------------------
# Phase 3 — tell the truth about what is still alive.
# ---------------------------------------------------------------------------
verify() {
    echo "[3/3] Final state:"
    local unit state any_up=0
    for unit in "${SERVICES[@]}"; do
        state="$(systemctl --user is-active "$unit" 2>/dev/null)"
        [[ "$state" == "active" ]] && any_up=1
        log "$(printf '%-32s %s' "$unit" "$state")"
    done

    local leftovers
    leftovers="$(find_strays | wc -l)"
    log "escaped processes still alive: $leftovers"

    if [[ "$any_up" -eq 0 && "$leftovers" -eq 0 ]]; then
        echo "AI Controller stopped."
        say "$PHRASE_DONE"
        return 0
    fi
    echo "AI Controller NOT fully stopped — see above."
    say "$PHRASE_PARTIAL"
    return 1
}

echo "=== AI Controller — stop-all ==="
[[ "$DRY_RUN" == "1" ]] && echo "(DRY RUN — nothing will be killed)"

# Order matters: warm the cache and announce BEFORE the teardown, while the
# audio path is still up. After stop_services the sink may be gone.
warm_phrases
say "$PHRASE_STOPPING"

stop_services
reap_strays
verify
