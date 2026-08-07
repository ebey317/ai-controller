#!/usr/bin/env bash
# verify-voice-config.sh
# ---------------------------------------------------------------------------
# Prove the voice pipeline actually works. Exit 0 = all green.
#
# WHY THIS EXISTS: on 2026-08-06 the pipeline was 100% dead — every dictation
# press captured zero bytes — while `systemctl --user is-active` reported
# `active` for every service. Liveness of a process says nothing about whether
# signal survives the trip. Every check below observes REAL DATA, not status.
#
# Run after any edit to ptt_pynput.py / voice_bridge.py, after a reboot, or
# any time dictation "feels off".
# ---------------------------------------------------------------------------
set -uo pipefail

CONF="$HOME/.config/ai-controller/config.env"
BRIDGE="http://127.0.0.1:8002"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAIL=0

pass() { printf "  \033[32mPASS\033[0m  %s\n" "$1"; }
fail() { printf "  \033[31mFAIL\033[0m  %s\n     ↳ fix: %s\n" "$1" "$2"; FAIL=1; }

echo "== ai-controller voice pipeline verification =="

# ── 1. Services running ────────────────────────────────────────────────────
for s in ptt-pynput voice-bridge; do
    if [ "$(systemctl --user is-active $s.service 2>&1)" = "active" ]; then
        pass "$s service running"
    else
        fail "$s service not running" "systemctl --user restart $s.service"
    fi
done

# ── 2. Mic actually produces samples ───────────────────────────────────────
# THE decisive check. A dead mic and a healthy mic look identical to systemd.
SRC="$(grep '^AUDIO_INPUT=' "$CONF" 2>/dev/null | cut -d= -f2-)"
if [ -z "$SRC" ]; then
    fail "AUDIO_INPUT unset in config.env" \
         "set AUDIO_INPUT=<name from: pactl list sources short | grep mono-fallback>"
elif ! pactl list sources short 2>/dev/null | grep -q "$SRC"; then
    fail "mic source '$SRC' not present" \
         "bash ~/ai-controller/scripts/reset-controller-audio.sh"
else
    timeout 3 parec --device "$SRC" --rate 24000 --channels 1 \
        --format s16le --raw > "$TMP/mic.raw" 2>/dev/null
    BYTES=$(stat -c%s "$TMP/mic.raw" 2>/dev/null || echo 0)
    RMS=$(python3 -c "
import array,math,sys
d=array.array('h')
try: d.frombytes(open('$TMP/mic.raw','rb').read())
except Exception: pass
print(f'{math.sqrt(sum(x*x for x in d)/len(d)):.0f}' if len(d) else '0')
" 2>/dev/null || echo 0)
    # 16000 bytes is ptt_pynput.py's own 'Too short' threshold.
    if [ "$BYTES" -lt 16000 ]; then
        fail "mic captured only ${BYTES}B in 3s (need >16000)" \
             "card reset is likely firing during capture — check _mute_tts() in ptt_pynput.py; spd-say -C must NOT count as a kill"
    elif [ "$RMS" = "0" ]; then
        fail "mic captured ${BYTES}B but RMS=0 (flat zeros)" \
             "headset mic is muted or powered off — check the controller mute button"
    else
        pass "mic capturing (${BYTES}B/3s, RMS=${RMS})"
    fi
fi

# ── 3. /speak must be fire-and-forget ──────────────────────────────────────
# Slow here == _speak() blocking the asyncio event loop == STT will time out.
S=$(date +%s%N)
curl -s -m 10 -X POST "$BRIDGE/speak" --data-urlencode "text=verification check" >/dev/null 2>&1
MS=$(( ($(date +%s%N) - S) / 1000000 ))
if [ "$MS" -lt 300 ]; then
    pass "/speak returns in ${MS}ms (non-blocking)"
else
    fail "/speak blocked ${MS}ms (want <300)" \
         "_speak() is being called directly from an async handler — wrap it: _speak_bg() / asyncio.to_thread"
fi

# ── 4. Full STT round-trip on real audio ───────────────────────────────────
if [ "${BYTES:-0}" -ge 16000 ]; then
    python3 -c "
import wave
d=open('$TMP/mic.raw','rb').read()
w=wave.open('$TMP/mic.wav','wb');w.setnchannels(1);w.setsampwidth(2)
w.setframerate(24000);w.writeframes(d);w.close()
" 2>/dev/null
    R=$(curl -s -m 45 -X POST "$BRIDGE/voice" -F "audio=@$TMP/mic.wav" \
        -F "mode=transcribe" 2>&1)
    if echo "$R" | grep -q '"transcript"'; then
        pass "STT round-trip OK — $(echo "$R" | python3 -c "import json,sys;print(repr(json.load(sys.stdin).get('transcript','')))" 2>/dev/null)"
    else
        fail "STT round-trip failed: $R" \
             "check GROQ_API_KEY in config.env and: tail -40 ~/ai-controller/logs/voice-bridge.log"
    fi
fi

# ── 5. TTS player must not have the lowpass filter back ────────────────────
# Strip comments first: the file legitimately DISCUSSES lowpass in a warning
# explaining why it must never come back. Matching the whole file flags its own
# documentation and cries wolf on a healthy config.
if grep -vE '^\s*#' "$HOME/ai-controller/scripts/hermes_tts_play.sh" 2>/dev/null \
   | grep -q "lowpass"; then
    fail "lowpass filter is back in hermes_tts_play.sh" \
         "remove it — it MASKS resampler aliasing by discarding everything >3kHz; the fix is soxr resampling"
else
    pass "TTS player clean (soxr, no lowpass)"
fi

echo
if [ "$FAIL" -eq 0 ]; then
    echo -e "\033[32m== ALL GREEN — pipeline verified end to end ==\033[0m"
else
    echo -e "\033[31m== FAILURES ABOVE — apply the listed fix ==\033[0m"
fi
exit "$FAIL"
