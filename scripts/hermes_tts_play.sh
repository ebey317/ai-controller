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

CONFIG_FILE="${HOME}/.config/ai-controller/config.env"
AUDIO_OUTPUT=""
if [[ -f "$CONFIG_FILE" ]]; then
    # shellcheck source=/dev/null
    AUDIO_OUTPUT=$(set -a; source "$CONFIG_FILE" 2>/dev/null; echo "${AUDIO_OUTPUT:-}")
fi

# --force-media-title=AI_TTS_BARGE tags this as TTS so the trigger (RT) can
# barge-in and kill it without touching IPTV/video mpv.
# Resolve sink. If the configured sink doesn't exist, find the Xbox/Microsoft
# headset sink dynamically so TTS doesn't fall back to the wrong device.
SINK_NAME="$AUDIO_OUTPUT"
if [[ -n "$SINK_NAME" ]] && pactl list sinks short 2>/dev/null | awk '{print $2}' | grep -qx "$SINK_NAME"; then
    SINK="pulse/${SINK_NAME}"
else
    SINK_NAME=$(pactl list sinks short 2>/dev/null | grep -iE "Microsoft_Controller|Xbox" | awk '{print $2}' | head -1)
    if [[ -n "$SINK_NAME" ]]; then
        SINK="pulse/${SINK_NAME}"
    fi
fi

# auto_null is PulseAudio's dummy sink — it appears when no real output device
# is present (controller unplugged, headless boot). Playing into it silently
# succeeds, which looks like working TTS and is not. Treat it as no sink and
# let the default-device branch below handle it.
if [[ "$SINK_NAME" == "auto_null" ]]; then
    SINK=""
    SINK_NAME=""
fi

# Audio route, traced end-to-end 2026-07-29:
#   edge-tts writes MP3 at 24000 Hz MONO
#     -> this player
#     -> PulseAudio sink (s16le 2ch 48000 Hz)
#     -> xone-gip-headset hardware (negotiated 48000/2, period 384)
#
# The sink runs at exactly 2x the source rate with double the channels, so
# something must resample. Left to PulseAudio that job falls to its default
# resample-method=speex-float-1 -- the lowest quality setting on a 0-10 scale
# -- whose 2x upsampling aliases badly in the top octaves and is audible as
# radio static on the headset.
#
# The old --af=lowpass=f=3000 was NOT device tuning: it masked those aliasing
# artifacts by discarding everything above 3 kHz. That is why speech sounded
# muffled with it and staticky without it. Both were symptoms of the same
# resampler bug.
#
# Correct fix: resample exactly ONCE, here, with soxr at high precision,
# straight into the sink's native rate/format/layout. PulseAudio then has
# nothing left to convert and passes the stream through untouched -- no
# artifacts to mask, and the full speech bandwidth survives.
# Do NOT re-add a lowpass filter; if static returns, the resample chain broke.
SINK_RATE=$(pactl list sinks 2>/dev/null \
    | grep -A 10 "Name: ${SINK_NAME}" \
    | grep -m1 "Sample Specification" \
    | grep -oE '[0-9]+Hz' | tr -d 'Hz')
[[ "$SINK_RATE" =~ ^[0-9]+$ ]] || SINK_RATE=48000

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
    mpv "${MPV_COMMON[@]}" "$@" "$AUDIO_FILE" 2>/dev/null
    rc=$?
    [[ $rc -eq 0 ]] && return 0
    if [[ $rc -ge 128 || $rc -eq 4 ]]; then
        return "$rc"   # stopped on purpose — do NOT resurrect
    fi
    # Genuine startup failure (e.g. libsoxr missing): retry without the filter.
    mpv "${MPV_FALLBACK[@]}" "$@" "$AUDIO_FILE" 2>/dev/null
}

if [[ -n "$SINK" ]]; then
    play --audio-device="$SINK"
else
    # No output device configured and no Xbox sink visible — use default sink.
    play
fi
