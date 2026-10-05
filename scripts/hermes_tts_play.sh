#!/bin/bash
# Hermes TTS playback wrapper - routes audio to the configured output device.
# Reads AUDIO_OUTPUT from ~/.config/ai-controller/config.env if available.

AUDIO_FILE="$1"

if [[ -z "$AUDIO_FILE" ]]; then
    echo "Usage: $0 <audio_file.mp3>"
    exit 1
fi

if [[ ! -f "$AUDIO_FILE" ]]; then
    echo "Error: File not found: $AUDIO_FILE"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TTS_CONTROL="$SCRIPT_DIR/tts_control.py"
PYTHON_BIN="$(command -v python3 || echo /usr/bin/python3)"
# stderr goes to a log, not /dev/null -- see scripts/tts_log.sh. Sourced before
# anything that can fail, so even this file's first subprocess is diagnosable.
# shellcheck source=scripts/tts_log.sh
if [ -f "$SCRIPT_DIR/tts_log.sh" ]; then
    source "$SCRIPT_DIR/tts_log.sh"
else
    # Partial checkout: degrade to discarding stderr rather than aborting.
    TTS_LOG=""
fi

CONFIG_FILE="${HOME}/.config/ai-controller/config.env"
AUDIO_OUTPUT=""
if [[ -f "$CONFIG_FILE" ]]; then
    # NOTE: AUDIO_OUTPUT is read but never used below (sink resolution is
    # tts_control.py's job). The source is kept for now, but its stderr is
    # logged rather than swallowed so a syntax error in config.env is visible.
    # shellcheck source=/dev/null
    AUDIO_OUTPUT=$(set -a; source "$CONFIG_FILE" 2>>"${TTS_LOG:-/dev/null}"; echo "${AUDIO_OUTPUT:-}")
fi

# --force-media-title=AI_TTS_BARGE tags this as TTS so the trigger (RT) can
# barge-in and kill it without touching IPTV/video mpv.
# Resolve sink. If the configured sink doesn't exist, find the Xbox/Microsoft
# headset sink dynamically so TTS doesn't fall back to the wrong device.
# 2026-09-30: sink and rate both come from scripts/tts_control.py, which
# resolves the user's chosen driver (hdmi / headphones) against the sinks that
# are actually present right now and falls back to the PulseAudio default when
# the chosen one is absent. This script previously did its own resolution and
# had two problems: it hunted only for a Microsoft/Xbox sink, so a machine
# listening through HDMI found nothing, and it read the sample rate from a sink
# name that could be empty, silently taking the first sink in the list rather
# than the one actually playing.
#
# Keep the local variables -- play() below still reads SINK and SINK_RATE.

SINK_NAME="$("$PYTHON_BIN" "$TTS_CONTROL" sink 2>>"${TTS_LOG:-/dev/null}" || true)"
SINK_RATE="$("$PYTHON_BIN" "$TTS_CONTROL" rate 2>>"${TTS_LOG:-/dev/null}" || true)"

if [ -n "$SINK_NAME" ]; then
    SINK="pulse/${SINK_NAME}"
else
    # tts_control unavailable -- fall back to whatever PulseAudio defaults to.
    # Its stderr was logged above; this branch is the visible consequence.
    SINK=""
    SINK_NAME="$(pactl get-default-sink 2>>"${TTS_LOG:-/dev/null}" || true)"
    SINK_RATE="$(pactl list sinks 2>>"${TTS_LOG:-/dev/null}" \
        | grep -A 10 "Name: ${SINK_NAME}" \
        | grep -m1 "Sample Specification" \
        | grep -oE '[0-9]+Hz' | tr -d 'Hz')"
fi
[[ "$SINK_RATE" =~ ^[0-9]+$ ]] || SINK_RATE=48000

# auto_null is PulseAudio's dummy sink -- it appears when no real output device
# is present. Playing into it silently succeeds, which looks like working TTS
# and is not. Treat it as no sink and let the default-device branch handle it.
if [[ "$SINK_NAME" == "auto_null" || "$SINK_NAME" == *null* ]]; then
    SINK=""
    SINK_NAME=""
fi

MPV_COMMON=(--no-video --force-media-title=AI_TTS_BARGE
            --audio-samplerate="$SINK_RATE"
            --audio-channels=stereo
            --audio-format=s16
            --af="lavfi=[aresample=${SINK_RATE}:resampler=soxr:precision=28]")

# Fallback drops only the soxr filter (mpv's built-in swresample still beats
# speex-float-1 by a wide margin) in case libsoxr is unavailable on this host.
MPV_FALLBACK=(--no-video --force-media-title=AI_TTS_BARGE
              --audio-samplerate="$SINK_RATE"
              --audio-channels=stereo
              --audio-format=s16)

# Run mpv, capture its output, and append only the useful part to tts.log.
#
# IMPORTANT: mpv 4.4 writes EVERYTHING to stdout — the `AO: [pulse] ...` line,
# `[ao] Failed to initialize audio driver`, even
# `Could not open/initialize audio device -> no sound.` Its stderr is empty.
# Capturing `2>` (the obvious reading of "send stderr to a log") therefore
# records nothing at all and the silent-speaker diagnosis stays blind. Both
# streams are captured together here.
#
# mpv also draws a terminal status bar with carriage returns. Pointed straight
# at the log that is ~900 bytes of `A: 00:00:01 / 00:00:02 (42%)` churn per
# utterance, which buries the lines worth having. `tr` splits each redraw into
# its own line so progress frames can be dropped without altering message text.
# (`--msg-level` does NOT help: mpv 4.4 ignores it for the status bar.)
#
# The exit status returned is mpv's, unchanged — play() depends on it to tell a
# deliberate stop (128+N, or 4 for "quit by user") from a genuine startup
# failure that deserves the fallback retry.
run_mpv() {
    local rc err
    err="$(mktemp "${TMPDIR:-/tmp}/tts-play.XXXXXX" 2>/dev/null)" || err=""
    if [[ -z "$err" ]]; then
        mpv "$@" >>"${TTS_LOG:-/dev/null}" 2>&1
        return $?
    fi
    mpv "$@" >"$err" 2>&1
    rc=$?
    tr '\r' '\n' < "$err" \
        | grep -avE 'A: [0-9]{2}:[0-9]{2}:[0-9]{2} /' \
        | grep -av '^$' >> "${TTS_LOG:-/dev/null}" 2>/dev/null
    rm -f "$err"
    return "$rc"
}

# Run the primary player, then decide whether a failure is worth retrying.
#
# CRITICAL: a plain `mpv ... || mpv ...` chain makes speech UNSTOPPABLE. When
# barge-in SIGTERMs the player it exits non-zero, `||` reads that as "mpv could
# not start", and relaunches it — so killing the audio restarts the audio.
# Observed 2026-07-30: barge-in killed PID 285297 and immediately got 285378.
#
# A process killed by signal N exits 128+N (SIGTERM=143, SIGKILL=137), and mpv
# uses 4 for "quit by user". None of those mean the fallback is needed; they
# mean someone deliberately stopped playback and we must stay stopped.
play() {
    local rc
    run_mpv "${MPV_COMMON[@]}" "$@" "$AUDIO_FILE"
    rc=$?
    [[ $rc -eq 0 ]] && return 0
    if [[ $rc -ge 128 || $rc -eq 4 ]]; then
        return "$rc"   # stopped on purpose — do NOT resurrect
    fi
    # Genuine startup failure (e.g. libsoxr missing): retry without the filter.
    run_mpv "${MPV_FALLBACK[@]}" "$@" "$AUDIO_FILE"
}

if [[ -n "$SINK" ]]; then
    play --audio-device="$SINK"
else
    # No output device configured and no Xbox sink visible — use default sink.
    play
fi
