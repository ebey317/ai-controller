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

# Wait for the sound server to be reachable (cold start safety).
for _ in $(seq 1 20); do pactl info >/dev/null 2>&1 && break; sleep 0.5; done

# Find the controller's audio card by NAME (portable — not tied to a serial).
CARD=$(pactl list cards short 2>/dev/null | grep -iE "Microsoft_Controller|Xbox" | awk '{print $2}' | head -1)
if [ -z "$CARD" ]; then
    echo "reset-controller-audio: no Xbox controller audio card present (nothing to reset)"
    exit 0
fi

# "off" mode (called on Stop): unplug the device so the next Start does a clean
# plug-in. Stop + Start = full unplug/replug cycle.
if [ "${1:-}" = "off" ]; then
    pactl set-card-profile "$CARD" off 2>/dev/null || true
    echo "reset-controller-audio: $CARD profile -> off (unplugged)"
    exit 0
fi

# Choose the 'on' profile. Default to combined (headset speakers + mic) so TTS
# is routed to the Xbox controller headphones. Set CONTROLLER_AUDIO_PROFILE=input
# to force input-only (rock-stable mic, no headset output).
WANT="${CONTROLLER_AUDIO_PROFILE:-combined}"
if [ "$WANT" = "input" ]; then
    ON_PROFILE=$(pactl list cards 2>/dev/null | awk "/Name: ${CARD}/,/Active Profile/" \
        | grep -oE "input:mono-fallback" | head -1)
else
    ON_PROFILE=$(pactl list cards 2>/dev/null | awk "/Name: ${CARD}/,/Active Profile/" \
        | grep -oE "output:stereo-fallback\\+input:mono-fallback" | head -1)
fi
[ -z "${ON_PROFILE:-}" ] && ON_PROFILE=$(pactl list cards 2>/dev/null | awk "/Name: ${CARD}/,/Active Profile/" \
    | grep -oE "input:mono-fallback" | head -1)
[ -z "${ON_PROFILE:-}" ] && ON_PROFILE="input:mono-fallback"

echo "reset-controller-audio: replug cycle on $CARD  ->  off -> $ON_PROFILE"
pactl set-card-profile "$CARD" off 2>/dev/null || true
sleep 0.7
pactl set-card-profile "$CARD" "$ON_PROFILE" 2>/dev/null || true
sleep 0.4

# Make the controller mic the default capture source and unmute/raise it.
SRC=$(pactl list sources short 2>/dev/null | grep -iE "Microsoft_Controller|Xbox" | grep -i "input" | awk '{print $2}' | head -1)
if [ -n "$SRC" ]; then
    pactl set-default-source "$SRC" 2>/dev/null || true
    pactl set-source-mute "$SRC" 0 2>/dev/null || true
    pactl set-source-volume "$SRC" 100% 2>/dev/null || true
fi

# When combined, also make the controller headphones the default playback sink.
if [ "$ON_PROFILE" != "input:mono-fallback" ]; then
    SINK=$(pactl list sinks short 2>/dev/null | grep -iE "Microsoft_Controller|Xbox" | awk '{print $2}' | head -1)
    if [ -n "$SINK" ]; then
        pactl set-default-sink "$SINK" 2>/dev/null || true
        pactl set-sink-mute "$SINK" 0 2>/dev/null || true
        pactl set-sink-volume "$SINK" 100% 2>/dev/null || true
    fi
fi

echo "reset-controller-audio: done ($(pactl list cards 2>/dev/null | awk "/Name: ${CARD}/,0" | grep -m1 'Active Profile' | sed 's/^[[:space:]]*//'))"
