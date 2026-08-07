#!/usr/bin/env bash
# snapshot-voice-config.sh
# ---------------------------------------------------------------------------
# Capture the CURRENT working state of the ai-controller voice pipeline so it
# can be restored byte-for-byte after a bad edit, a reinstall, or a disk move.
#
# Elijah has no mouse or keyboard. This pipeline is his only input channel, so
# "it works right now" is a state worth freezing, not just remembering.
#
# WHAT GOES WHERE:
#   snapshots/<ts>/           committed to git  — code shas, units, manifest
#   ~/.config/ai-controller/backups/   NEVER in git — real config.env w/ API key
#
# config.env holds a live GROQ_API_KEY. It is deliberately kept out of the repo
# copy; only a redacted template is snapshotted there.
# ---------------------------------------------------------------------------
set -uo pipefail

REPO="${AI_CONTROLLER_DIR:-$HOME/ai-controller}"
TS="$(date +%Y-%m-%d_%H%M%S)"
SNAP="$REPO/snapshots/$TS"
SECRETS="$HOME/.config/ai-controller/backups"
CONF="$HOME/.config/ai-controller/config.env"
UNITDIR="$HOME/.config/systemd/user"
SERVICES=(ptt-pynput voice-bridge gip-audio-watchdog)

mkdir -p "$SNAP/units" "$SECRETS"
chmod 700 "$SECRETS"

# ── 1. Secrets: real config.env, outside the repo, owner-only ───────────────
if [ -f "$CONF" ]; then
    cp "$CONF" "$SECRETS/config.env.$TS"
    chmod 600 "$SECRETS/config.env.$TS"
    # Redacted template for the repo copy: keys blanked, device names kept
    # (the device names are the part that actually matters for restore).
    sed -E 's/^([A-Z_]*(KEY|TOKEN|SECRET|PASSWORD)[A-Z_]*)=.*/\1=***SET_ME***/' \
        "$CONF" > "$SNAP/config.env.template"
fi

# ── 2. systemd units (these are what make it survive reboot) ────────────────
for s in "${SERVICES[@]}"; do
    [ -f "$UNITDIR/$s.service" ] && cp "$UNITDIR/$s.service" "$SNAP/units/"
done

# ── 3. Manifest: everything needed to prove/rebuild this exact state ────────
{
    echo "# ai-controller voice pipeline snapshot"
    echo "taken:    $TS"
    echo "host:     $(hostname)"
    echo "kernel:   $(uname -r)"
    echo

    echo "## git"
    echo "branch:   $(git -C "$REPO" branch --show-current 2>/dev/null)"
    echo "commit:   $(git -C "$REPO" rev-parse HEAD 2>/dev/null)"
    echo "dirty:    $(git -C "$REPO" status --porcelain 2>/dev/null | wc -l) file(s) uncommitted"
    echo

    echo "## file checksums (the working code)"
    for f in scripts/ptt_pynput.py scripts/voice_bridge.py \
             scripts/hermes_tts_play.sh scripts/reset-controller-audio.sh \
             scripts/gip-audio-watchdog.sh; do
        [ -f "$REPO/$f" ] && sha256sum "$REPO/$f" | sed "s|$REPO/||"
    done
    echo

    echo "## services"
    for s in "${SERVICES[@]}"; do
        printf "%-22s enabled=%-10s active=%s\n" "$s" \
            "$(systemctl --user is-enabled "$s.service" 2>&1)" \
            "$(systemctl --user is-active "$s.service" 2>&1)"
    done
    echo "linger:   $(loginctl show-user "$USER" 2>/dev/null | grep -i linger)"
    echo

    echo "## audio topology (mic and ears are DIFFERENT devices)"
    echo "AUDIO_INPUT (mic):  $(grep '^AUDIO_INPUT='  "$CONF" 2>/dev/null | cut -d= -f2-)"
    echo "AUDIO_OUTPUT(ears): $(grep '^AUDIO_OUTPUT=' "$CONF" 2>/dev/null | cut -d= -f2-)"
    echo
    echo "### pactl cards"
    pactl list cards short 2>/dev/null
    echo "### pactl sources"
    pactl list sources short 2>/dev/null
    echo "### pactl sinks"
    pactl list sinks short 2>/dev/null
    echo

    echo "## python env"
    # BOTH services run from the repo venv, NOT system python3. pynput/evdev are
    # installed only there — probing system python3 gives a false 'missing'.
    VPY="$REPO/.venv/bin/python3"
    echo "interpreter: $VPY"
    if [ -x "$VPY" ]; then
        echo "version:  $("$VPY" --version 2>&1)"
        "$VPY" -m pip freeze 2>/dev/null > "$SNAP/requirements.freeze.txt"
        echo "frozen:   $(wc -l < "$SNAP/requirements.freeze.txt") packages -> requirements.freeze.txt"
        grep -iE '^(fastapi|uvicorn|httpx|pynput|evdev|python-multipart|edge-tts)' \
            "$SNAP/requirements.freeze.txt" 2>/dev/null | sed 's/^/  /'
    else
        echo "!! venv MISSING at $VPY — services cannot start"
    fi
    echo

    echo "## known-good measurements (see verify-voice-config.sh)"
    echo "mic 3s capture:   >16000 bytes (healthy ~143000), RMS > 0"
    echo "POST /speak:      < 0.100s   (blocking bug made this 1.279s)"
    echo "POST /voice:      returns transcript even while TTS is speaking"
} > "$SNAP/MANIFEST.txt" 2>&1

# ── 4. Point "latest" at this snapshot ─────────────────────────────────────
ln -sfn "$TS" "$REPO/snapshots/latest"

echo "snapshot: $SNAP"
echo "secrets:  $SECRETS/config.env.$TS  (chmod 600, NOT in git)"
