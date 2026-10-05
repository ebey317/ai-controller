#!/bin/bash
# Shared stderr capture for the TTS pipeline.
#
# WHY THIS EXISTS (2026-09-30)
# -----------------------------
# hermes_tts_play.sh, tts_stop.sh and reset-controller-audio.sh used to send
# every helper's stderr to /dev/null. That made each failure look exactly like
# success: a missing python, a crashed tts_control.py, a pactl call that
# failed, even the -28 wedge that reset-controller-audio.sh exists to fix were
# all discarded. The live "TTS is on but I hear nothing" symptom was
# undiagnosable for days for this reason alone.
#
# Redirect to a file instead. Failures keep their exit codes -- this only
# makes the messages recoverable.
#
# Usage:   source "$SCRIPT_DIR/tts_log.sh"
#          some-command 2>>"${TTS_LOG:-/dev/null}"
#
# TTS_LOG is "" when the state directory cannot be created, so every caller
# must use the ${TTS_LOG:-/dev/null} form rather than $TTS_LOG directly.

TTS_LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/ai-controller"
TTS_LOG=""

if mkdir -p "$TTS_LOG_DIR" 2>/dev/null && [ -w "$TTS_LOG_DIR" ]; then
    TTS_LOG="$TTS_LOG_DIR/tts.log"

    # Cap the log so a chatty player (mpv writes a status line per frame to
    # stderr) cannot fill the disk. One previous generation is plenty for a
    # local diagnosis and keeps `tail` readable.
    _TTS_LOG_MAX=1048576
    if [ -f "$TTS_LOG" ]; then
        _TTS_LOG_SIZE=$(wc -c < "$TTS_LOG" 2>/dev/null || echo 0)
        if [ "${_TTS_LOG_SIZE:-0}" -gt "$_TTS_LOG_MAX" ] 2>/dev/null; then
            mv -f "$TTS_LOG" "$TTS_LOG.1" 2>/dev/null || true
        fi
        unset _TTS_LOG_SIZE
    fi
    unset _TTS_LOG_MAX
fi

# Timestamp each invocation so a log entry can be tied to the script run that
# produced it. Failures here are irrelevant -- the caller never depends on it.
{
    printf '\n--- %s %s[%s] ---\n' "$(date '+%F %T')" "$(basename -- "${0:-tts}")" "$$"
} >> "$TTS_LOG" 2>/dev/null || true
