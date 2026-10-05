#!/usr/bin/env bash
# reset-controller-audio.sh
# ---------------------------------------------------------------------------
# Software "unplug + replug" for the Xbox controller headset.
#
# WHY: the xone-gip-headset driver wedges its audio OUTPUT buffers over time
#   (dmesg: "xone-gip gip0: gip_send_audio_samples: get buffer failed: -28").
#   That buffer thrash starves the MIC capture until you physically unplug and
#   replug the headset. Cycling the PulseAudio card profile off->on performs
#   the same device re-initialization in software — no hands needed.
#
# This is called by the launcher on Start (and Stop) so dictation "just works"
# every time, like a real standalone product.
#
# DEFAULT profile = input-only ("input:mono-fallback"): the mic NEVER wedges in
#   this mode because the failing output path is not active. Headset speaker
#   output is off; system audio plays through the normal default sink instead.
#   Set CONTROLLER_AUDIO_PROFILE=combined to keep headset speakers + mic (like a
#   physical replug) at the cost of the -28 wedge possibly returning over a long
#   session — in which case a Stop/Start re-resets it.
# ---------------------------------------------------------------------------
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# stderr goes to a log, not /dev/null -- see scripts/tts_log.sh. Every pactl
# failure below used to be discarded, including the very -28 wedge this script
# exists to fix, so "done" could be printed while the card was still broken.
# shellcheck source=scripts/tts_log.sh
if [ -f "$SCRIPT_DIR/tts_log.sh" ]; then
    source "$SCRIPT_DIR/tts_log.sh"
else
    # Partial checkout: degrade to discarding stderr rather than aborting.
    TTS_LOG=""
fi

# Wait for the sound server to be reachable (cold start safety).
sound_server_up=0
for _ in $(seq 1 20); do
    if pactl info >>"${TTS_LOG:-/dev/null}" 2>&1; then sound_server_up=1; break; fi
    sleep 0.5
done
if [ "$sound_server_up" -ne 1 ]; then
    echo "reset-controller-audio: WARNING pulseaudio never became reachable" >&2
    [ -n "${TTS_LOG:-}" ] && echo "  Details: $TTS_LOG" >&2
fi

# Find the controller's audio card by NAME (portable — not tied to a serial).
CARD=$(pactl list cards short 2>>"${TTS_LOG:-/dev/null}" | grep -iE "Microsoft_Controller|Xbox" | awk '{print $2}' | head -1)
if [ -z "$CARD" ]; then
    echo "reset-controller-audio: no Xbox controller audio card present (nothing to reset)"
    exit 0
fi

# "off" mode (called on Stop): unplug the device so the next Start does a clean
# plug-in. Stop + Start = full unplug/replug cycle.
if [ "${1:-}" = "off" ]; then
    pactl set-card-profile "$CARD" off 2>>"${TTS_LOG:-/dev/null}" || true
    echo "reset-controller-audio: $CARD profile -> off (unplugged)"
    exit 0
fi

# Choose the 'on' profile.
#
# 2026-09-30: this header used to claim the default profile was input-only
# while the code below defaulted to combined. The two disagreed, which is how
# the card ended up stuck at input-only on a machine whose user wanted headset
# output available on demand.
#
# The profile now follows the user's explicit driver choice (AI_TTS_DRIVER in
# config.env; see scripts/tts_control.py):
#   * headphones -> combined  = "output:stereo-fallback+input:mono-fallback"
#       headset speakers + mic, like a physical replug. This is the profile
#       the xone-gip driver is documented to wedge (-28) over a long session,
#       which is why it is not left running when it is not wanted.
#   * anything else (hdmi, default) -> input = "input:mono-fallback"
#       mic only; the failing output path is not active, so it cannot wedge.
#
# CONTROLLER_AUDIO_PROFILE still overrides both, for debugging a wedge.
if [ -n "${CONTROLLER_AUDIO_PROFILE:-}" ]; then
    WANT="$CONTROLLER_AUDIO_PROFILE"
else
    _TC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/tts_control.py"
    _PY="$(command -v python3 || echo /usr/bin/python3)"
    _CHOSEN_DRIVER="$("$_PY" "$_TC" driver 2>>"${TTS_LOG:-/dev/null}" || true)"
    if [ "$_CHOSEN_DRIVER" = "headphones" ]; then
        WANT="combined"
    else
        WANT="input"
    fi
fi
if [ "$WANT" = "input" ]; then
    ON_PROFILE=$(pactl list cards 2>>"${TTS_LOG:-/dev/null}" | awk "/Name: ${CARD}/,/Active Profile/" \
        | grep -oE "input:mono-fallback" | head -1)
else
    ON_PROFILE=$(pactl list cards 2>>"${TTS_LOG:-/dev/null}" | awk "/Name: ${CARD}/,/Active Profile/" \
        | grep -oE "output:stereo-fallback\\+input:mono-fallback" | head -1)
fi
[ -z "${ON_PROFILE:-}" ] && ON_PROFILE=$(pactl list cards 2>>"${TTS_LOG:-/dev/null}" | awk "/Name: ${CARD}/,/Active Profile/" \
    | grep -oE "input:mono-fallback" | head -1)
[ -z "${ON_PROFILE:-}" ] && ON_PROFILE="input:mono-fallback"

echo "reset-controller-audio: replug cycle on $CARD  ->  off -> $ON_PROFILE"
pactl set-card-profile "$CARD" off 2>>"${TTS_LOG:-/dev/null}" || true
sleep 0.7
pactl set-card-profile "$CARD" "$ON_PROFILE" 2>>"${TTS_LOG:-/dev/null}" || true
sleep 0.4

# Make the controller mic the default capture source and unmute/raise it.
SRC=$(pactl list sources short 2>>"${TTS_LOG:-/dev/null}" | grep -iE "Microsoft_Controller|Xbox" | grep -i "input" | awk '{print $2}' | head -1)
if [ -n "$SRC" ]; then
    pactl set-default-source "$SRC" 2>>"${TTS_LOG:-/dev/null}" || true
    pactl set-source-mute "$SRC" 0 2>>"${TTS_LOG:-/dev/null}" || true
    pactl set-source-volume "$SRC" 100% 2>>"${TTS_LOG:-/dev/null}" || true
fi

# 2026-09-30: the "make the controller headset the default sink" step that
# used to live here was removed. It ran on every launcher Start, so merely
# opening the app moved ALL system audio onto the controller headset --
# wrong on a machine that listens through HDMI most of the time.
#
# Output routing is now owned by scripts/tts_control.py (AI_TTS_DRIVER) and
# applied per stream by hermes_tts_play.sh, which targets the selected sink
# directly instead of moving the machine-wide default. The headset is still
# unmuted and raised here when it is the active profile, so a freshly cycled
# card is not left silent.
if [ "$ON_PROFILE" != "input:mono-fallback" ]; then
    SINK=$(pactl list sinks short 2>>"${TTS_LOG:-/dev/null}" | grep -iE "Microsoft_Controller|Xbox" | awk '{print $2}' | head -1)
    if [ -n "$SINK" ]; then
        pactl set-sink-mute "$SINK" 0 2>>"${TTS_LOG:-/dev/null}" || true
        pactl set-sink-volume "$SINK" 100% 2>>"${TTS_LOG:-/dev/null}" || true
    fi
fi

# Verify the cycle actually landed. This used to print "done" unconditionally,
# so a set-card-profile that failed -- or a wedge that reasserted itself --
# looked identical to a clean reset. Escape the card name before handing it to
# awk: it contains dots (alsa_card.pci-...), which are regex metacharacters.
CARD_RE=$(printf '%s' "$CARD" | sed 's/[][\\.*^$]/\\&/g')
ACTUAL=$(pactl list cards 2>>"${TTS_LOG:-/dev/null}" \
    | awk "/Name: ${CARD_RE}/,/^Card #/" \
    | grep -m1 'Active Profile' \
    | sed 's/^[[:space:]]*Active Profile:[[:space:]]*//')

if [ "$ACTUAL" = "$ON_PROFILE" ]; then
    echo "reset-controller-audio: done (Active Profile: $ACTUAL)"
else
    echo "reset-controller-audio: FAILED -- wanted '$ON_PROFILE', card reports '${ACTUAL:-<none>}'" >&2
    [ -n "${TTS_LOG:-}" ] && echo "  Details: $TTS_LOG" >&2
fi
