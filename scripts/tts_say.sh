#!/bin/bash
# tts_say.sh — speak a line of text through Hermes TTS, without static.
#
#   Usage:  tts_say.sh "AI controller stopped"
#           tts_say.sh --warm "phrase"      # synthesize + cache, do not play
#
# WHY THIS EXISTS
# ---------------
# Two problems this file solves at once.
#
# 1. STATIC. edge-tts emits MP3 at 24000 Hz MONO. The controller sink runs
#    48000 Hz STEREO. Something must resample. Left to PulseAudio, that job
#    falls to resample-method=speex-float-1 — lowest quality on a 0-10 scale
#    — and its exact-2x upsample aliases in the top octaves. That aliasing IS
#    the radio static. (Traced end-to-end 2026-07-29.)
#
#    A lowpass filter appears to fix it and does not: it discards everything
#    above 3 kHz, trading static for muffle. Both are symptoms of one bug.
#    We never filter. We resample ONCE, correctly, via hermes_tts_play.sh,
#    which hands PulseAudio a stream already in the sink's native format so
#    PA passes it through untouched (`Resample method: n/a`).
#
# 2. THE BOOTSTRAP PROBLEM. This is called by stop-all.sh — the kill button.
#    A kill button that needs the voice stack alive to announce that it is
#    killing the voice stack is a kill button that fails exactly when used.
#    edge-tts also needs network, and this box runs off-grid on solar.
#
#    So: synthesis is cached by content hash. A phrase is generated at most
#    once, ever, and every later call is a pure file read + playback. The
#    kill button works offline, mid-teardown, on battery.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLAYER="$HERE/hermes_tts_play.sh"

# Hermes' own venv is the canonical TTS engine on both machines — we speak
# with the same voice Hermes does rather than installing a second stack.
EDGE_TTS=""
for cand in \
    "$HOME/.hermes/hermes-agent/venv/bin/edge-tts" \
    "$HOME/ai-controller/.venv/bin/edge-tts" \
    "$(command -v edge-tts 2>/dev/null)"; do
    [[ -n "$cand" && -x "$cand" ]] && { EDGE_TTS="$cand"; break; }
done

# Share Hermes' cache directory — same audio, one place, survives reboots.
CACHE_DIR="${TTS_CACHE_DIR:-$HOME/hermes/audio_cache}"
mkdir -p "$CACHE_DIR" 2>/dev/null

WARM_ONLY=0
case "${1:-}" in
    --warm) WARM_ONLY=1; shift ;;
    # Convenience passthrough so one entry point covers say and shut-up.
    --stop) exec "$HERE/tts_stop.sh" ;;
    --status) exec "$HERE/tts_stop.sh" --status ;;
esac
TEXT="${*:-}"
[[ -z "$TEXT" ]] && { echo "Usage: $0 [--warm|--stop|--status] <text>" >&2; exit 1; }

# Speaking a new line supersedes whatever is speaking now — otherwise replies
# pile up and overlap on the headset.
[[ "$WARM_ONLY" == "0" ]] && "$HERE/tts_stop.sh" >/dev/null 2>&1

# Resolve the configured voice to an edge-tts voice id, via the same
# voices/<id>/config.json that voice_toggle.py reads. Falls back to Aria.
config_dir="$HOME/.config/ai-controller"
voice_id="$(cat "$config_dir/ai_controller_voice" 2>/dev/null || echo joe)"
voice_json="$HERE/../voices/$voice_id/config.json"
EDGE_VOICE="$(sed -n 's/.*"voice"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$voice_json" 2>/dev/null | head -1)"
[[ -z "$EDGE_VOICE" ]] && EDGE_VOICE="en-US-AriaNeural"

# Cache key covers text AND voice, so switching voices re-synthesizes rather
# than silently replaying the old one.
KEY="$(printf '%s|%s' "$TEXT" "$EDGE_VOICE" | sha256sum | cut -c1-16)"
CACHED="$CACHE_DIR/say_${KEY}.mp3"

synthesize() {
    [[ -z "$EDGE_TTS" ]] && return 1
    local tmp="${CACHED}.part"
    timeout 20 "$EDGE_TTS" --voice "$EDGE_VOICE" --text "$TEXT" \
        --write-media "$tmp" >/dev/null 2>&1 || { rm -f "$tmp"; return 1; }
    # Only publish a non-empty result, so a truncated download can never
    # poison the cache permanently.
    if [[ -s "$tmp" ]]; then mv -f "$tmp" "$CACHED"; return 0; fi
    rm -f "$tmp"; return 1
}

# No cached audio AND synthesis unavailable — no network, edge-tts missing, or
# we are mid-teardown. This is the worst case for a kill button: off-grid, and
# you still need to know whether the stack actually died.
#
# There is no offline speech synth on either box (no espeak-ng, no piper), so
# words are not an option. A distinct two-tone chirp is arguably BETTER than
# speech here: it cannot be mistaken for a normal spoken reply, so it reads
# unambiguously as "the announcement itself failed" rather than as content.
#
# Belt and braces, in descending order of how likely you are to notice:
#   1. audible tone   — works when you are not looking at the screen
#   2. desktop notice — works when audio is the thing that is broken
#   3. syslog line    — notifies nobody now, but survives for the postmortem
# Success = the tone played. The other two are best-effort.
fallback_notify() {
    local text="$1"
    local played=1

    # Generated rather than loaded from disk: lavfi is always present with
    # mpv/ffplay, whereas /usr/share/sounds contents vary per distro.
    local tone='sine=frequency=660:duration=0.18,sine=frequency=440:duration=0.18'

    if command -v mpv >/dev/null 2>&1; then
        timeout 5 mpv --no-video --really-quiet \
            --force-media-title=AI_TTS_BARGE \
            "av://lavfi:aevalsrc=0.3*sin(2*PI*660*t):d=0.18" \
            >/dev/null 2>&1 && played=0
    fi

    if [[ "$played" != "0" ]] && command -v ffplay >/dev/null 2>&1; then
        timeout 5 ffplay -nodisp -autoexit -loglevel quiet \
            -f lavfi "$tone" >/dev/null 2>&1 && played=0
    fi

    # Last resort: any system sound file we can find.
    if [[ "$played" != "0" ]] && command -v paplay >/dev/null 2>&1; then
        local s
        for s in /usr/share/sounds/freedesktop/stereo/dialog-warning.oga \
                 /usr/share/sounds/freedesktop/stereo/bell.oga \
                 /usr/share/sounds/freedesktop/stereo/message.oga; do
            [[ -f "$s" ]] && timeout 5 paplay "$s" >/dev/null 2>&1 && { played=0; break; }
        done
    fi

    command -v notify-send >/dev/null 2>&1 && \
        notify-send -u critical "AI Controller (TTS unavailable)" "$text" >/dev/null 2>&1
    command -v logger >/dev/null 2>&1 && \
        logger -t ai-controller-tts -p user.warning "TTS unavailable, not spoken: $text"

    return "$played"
}

# --- main -------------------------------------------------------------------
if [[ ! -s "$CACHED" ]]; then
    synthesize || {
        [[ "$WARM_ONLY" == "1" ]] && exit 1
        fallback_notify "$TEXT"
        exit $?
    }
fi

[[ "$WARM_ONLY" == "1" ]] && { echo "cached: $CACHED"; exit 0; }

# Playback goes through the ONE corrected player. Never call mpv directly
# here — that is how the soxr fix got bypassed on the other machine.
if [[ -x "$PLAYER" ]]; then
    "$PLAYER" "$CACHED"
else
    echo "tts_say: player missing at $PLAYER" >&2
    exit 1
fi
