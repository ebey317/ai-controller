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

if [[ "${1:-}" == "--status" ]]; then
    echo "=== currently speaking ==="
    found=0
    while read -r pid; do
        [[ -z "$pid" ]] && continue
        echo "  PID $pid  $(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | cut -c1-80)"
        found=1
    done < <(speaking_pids)
    [[ "$found" -eq 0 ]] && echo "  (silent)"
    exit 0
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

if [[ "$killed" -gt 0 ]]; then
    echo "TTS stopped ($killed player(s))."
else
    echo "Nothing was speaking."
fi
exit 0
