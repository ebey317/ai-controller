#!/bin/bash
# tts_stop.sh — stop TTS playback RIGHT NOW, without touching anything else.
#
#   Usage:  tts_stop.sh          # stop speech, leave video/music alone
#           tts_stop.sh --status # show what is currently speaking
#
# WHY A SEPARATE SCRIPT
# ---------------------
# "Stop the stack" and "stop the talking" are different needs. stop-all.sh
# tears down services and leaves you with no dictation. Most of the time what
# you actually want is: shut up, right now, keep working. That is this.
#
# WHAT IT WILL AND WILL NOT KILL
# ------------------------------
# Killing "mpv" would take down IPTV and any video you are watching. So every
# TTS player is tagged --force-media-title=AI_TTS_BARGE, and we match that tag
# instead of the binary name. Same contract ptt_pynput.py's _mute_tts() uses,
# so RT barge-in and this script agree on what counts as speech.
#
# Covered:
#   - controller voice stack ...... tagged mpv (AI_TTS_BARGE)
#   - legacy Piper dictation ...... /tmp/ai_controller_tts.wav
#   - Hermes built-in TTS ......... ffplay/aplay on /tmp/hermes_voice/*.mp3
# Deliberately NOT covered: untagged mpv (your video), IPTV, music.
set -uo pipefail

# Bracketed first char stops the pattern from matching this script's own argv
# or the pgrep/pkill process itself — the classic pgrep -f self-match footgun.
#
# Keep this list in sync with _mute_tts() in ptt_pynput.py — that is the RT
# barge-in path and this is the manual one; they must agree on what "speech"
# means or one will stop something the other cannot.
# Matched against the cmdline of processes already confirmed to BE players
# (see speaking_pids), so plain substrings are safe here — no bracket tricks
# needed, and none should be added: they would fail to match the real thing.
PATTERNS=(
    'AI_TTS_BARGE'
    'ai_controller_tts'
    'hermes_voice'
    'hermes/audio_cache'
)
# NOTE: deliberately NO 'spd-say' pattern here. `pgrep -f spd-say` matches any
# command line that merely MENTIONS the string — including a shell running a
# script that contains it — and will happily kill your own terminal. Killing
# the client is useless anyway: speech-dispatcher is a daemon and holds the
# queued audio itself. `spd-say -C` below is the correct and only cancel.

# speech-dispatcher is a daemon: killing the spd-say client does not stop
# audio already queued inside it. -C cancels the queue itself.
cancel_speech_dispatcher() {
    command -v spd-say >/dev/null 2>&1 && timeout 3 spd-say -C >/dev/null 2>&1
    return 0
}

# Find speaking processes WITHOUT the pgrep -f footgun.
#
# `pgrep -f AI_TTS_BARGE` matches any process whose command line merely
# mentions the string — a shell running a script that greps for it, an editor
# with the file open, this script itself. That is not theoretical: it killed a
# live terminal twice while building this. Bracketing the first character only
# hides pgrep's own argv, not third parties.
#
# So: enumerate actual player BINARIES by exact process name, then inspect
# each one's cmdline. A shell is never named `mpv`, so it can never match.
PLAYER_BINS=(mpv ffplay aplay paplay)

speaking_pids() {
    local bin pid cmd pat
    for bin in "${PLAYER_BINS[@]}"; do
        for pid in $(pgrep -x "$bin" 2>/dev/null); do
            [[ "$pid" == "$$" ]] && continue
            [[ -r "/proc/$pid/cmdline" ]] || continue
            cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)"
            for pat in "${PATTERNS[@]}"; do
                if [[ "$cmd" =~ $pat ]]; then echo "$pid"; break; fi
            done
        done
    done | sort -un
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TTS_CONTROL="$SCRIPT_DIR/tts_control.py"
PYTHON_BIN="$(command -v python3 || echo /usr/bin/python3)"
# stderr goes to a log, not /dev/null -- see scripts/tts_log.sh.
# shellcheck source=scripts/tts_log.sh
if [ -f "$SCRIPT_DIR/tts_log.sh" ]; then
    source "$SCRIPT_DIR/tts_log.sh"
else
    # Partial checkout: degrade to discarding stderr rather than aborting.
    TTS_LOG=""
fi

if [[ "${1:-}" == "--status" ]]; then
    echo "=== currently speaking ==="
    found=0
    # 2026-09-30: playback streams first. Hermes plays TTS through a
    # sounddevice OutputStream inside its own python process, so it appears as
    # a PulseAudio stream and NOT as a player binary -- this script used to
    # report "silent" while the agent was still talking.
    if [[ -f "$TTS_CONTROL" ]]; then
        # Capture combined output: a helper that crashes must be reported as a
        # crash, never as "0 streams". Its stderr used to be discarded here,
        # which made a broken helper indistinguishable from silence.
        helper_out="$("$PYTHON_BIN" "$TTS_CONTROL" streams 2>&1)"
        helper_rc=$?
        [[ -n "${TTS_LOG:-}" ]] && printf '%s\n' "$helper_out" >> "$TTS_LOG" 2>/dev/null
        if [[ "$helper_rc" -ne 0 ]]; then
            echo "  helper tts_control.py streams FAILED (rc=$helper_rc)"
            printf '%s\n' "$helper_out" | head -5 | sed 's/^/      /'
            found=1
        else
            while read -r line; do
                # Only indent real stream lines; the "no TTS playback streams"
                # message is a sentence, not an entry.
                [[ -z "$line" || "$line" == "no TTS playback streams" ]] && continue
                echo "  stream ${line}"
                found=1
            done < <(printf '%s\n' "$helper_out" | sed 's/^ *//')
        fi
    else
        echo "  helper tts_control.py is MISSING — stream status unavailable"
        found=1
    fi
    while read -r pid; do
        [[ -z "$pid" ]] && continue
        echo "  PID $pid  $(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | cut -c1-80)"
        found=1
    done < <(speaking_pids)
    [[ "$found" -eq 0 ]] && echo "  (silent)"
    exit 0
fi

# 2026-09-30: the primary stop is now the same stream-level silence the RT
# barge-in uses, so this script and the trigger cannot disagree about what
# counts as speech. It is the only mechanism that catches Hermes' in-process
# playback; the process scan below remains for tagged players writing straight
# to ALSA, which never become a stream.
stream_stopped=0
stream_state="ok"
if [[ -f "$TTS_CONTROL" ]]; then
    helper_out="$("$PYTHON_BIN" "$TTS_CONTROL" mute-streams 2>&1)"
    helper_rc=$?
    [[ -n "${TTS_LOG:-}" ]] && printf '%s\n' "$helper_out" >> "$TTS_LOG" 2>/dev/null
    stream_stopped="$(printf '%s\n' "$helper_out" | head -1)"
    if [[ "$helper_rc" -ne 0 ]]; then
        stream_state="exited rc=$helper_rc"
    elif ! [[ "$stream_stopped" =~ ^[0-9]+$ ]]; then
        stream_state="gave no usable output"
    fi
    [[ "$stream_stopped" =~ ^[0-9]+$ ]] || stream_stopped=0
else
    stream_state="is missing"
fi

killed=0
for pid in $(speaking_pids); do
    kill -TERM "$pid" 2>/dev/null && killed=$((killed + 1))
done

# Always cancel the dispatcher queue, even if no spd-say client was running —
# audio can already be buffered inside the daemon with no client left to kill.
cancel_speech_dispatcher

# Give TERM a moment to land, then insist. mpv occasionally ignores the first
# signal while it is draining its audio buffer.
if [[ "$killed" -gt 0 ]]; then
    sleep 0.15
    for pid in $(speaking_pids); do
        kill -KILL "$pid" 2>/dev/null
    done
fi

if [[ "$killed" -gt 0 || "$stream_stopped" -gt 0 ]]; then
    echo "TTS stopped (${stream_stopped} stream(s), ${killed} player(s))."
elif [[ "$stream_state" == "ok" ]]; then
    echo "Nothing was speaking."
else
    echo "Nothing was stopped."
fi

# A failed helper must never be reported as success: stream-level silence is
# the ONLY mechanism that reaches Hermes' in-process playback, so when the
# helper is down the process scan below cannot do the job and the user has to
# know that rather than see a reassuring "0 streams".
if [[ "$stream_state" != "ok" ]]; then
    echo "  WARNING: tts_control.py $stream_state." >&2
    echo "  Stream-level silence did NOT happen. Only process-kill ran, which" >&2
    echo "  cannot reach Hermes' in-process sounddevice playback." >&2
    if [[ -n "${TTS_LOG:-}" ]]; then
        echo "  Details: $TTS_LOG" >&2
    fi
fi
exit 0
