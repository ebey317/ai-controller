#!/usr/bin/env bash
# AI Controller — Consumer installer
# Run: bash install.sh
# Sets up a standalone, reboot-safe AI Controller on Linux (Ubuntu/Mint/Debian).
# Requires: wired Xbox Series X/S controller, Groq API key, and internet access.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${HOME}/ai-controller"
CONFIG_DIR="${HOME}/.config/ai-controller"
SERVICE_DIR="${HOME}/.config/systemd/user"
ANTIMICROX_PROFILE_DIR="${HOME}/.config/antimicrox"

SERVICES=(
    antimicrox-autoload.service
    f13-xmodmap-heal.service
    controller-legend.service
    ptt-pynput.service
    voice-bridge.service
    ai-slide-keyboard.service
)

echo "======================================"
echo "  AI Controller Installer"
echo "======================================"
echo ""
echo "⚠️  PREREQUISITES: You need a Groq API key (free at https://console.groq.com/keys)"
echo "   and an active internet connection for voice dictation to work."
echo ""

# ── 1. OS CHECK ──────────────────────────────────────────────────────────────
if [[ "$OSTYPE" != "linux-gnu"* ]]; then
    echo "ERROR: This installer supports Linux (Ubuntu/Mint/Debian) only." >&2
    echo "       $OSTYPE is not supported." >&2
    exit 1
fi

echo "→ Platform: Linux (Ubuntu/Mint/Debian)"

# ── 1b. WAYLAND CHECK ────────────────────────────────────────────────────────
if [[ "$XDG_SESSION_TYPE" == "wayland" ]]; then
    echo "⚠️  WARNING: Wayland session detected."
    echo "   AI Controller uses xdotool and xclip, which require X11."
    echo "   On Wayland, text injection and clipboard may not work."
    echo "   For best results, log in with an Xorg/X11 session."
    echo ""
fi

# ── 2. INSTALL SYSTEM DEPENDENCIES ───────────────────────────────────────────
if [[ "${AI_CONTROLLER_SKIP_DEPS:-}" == "1" ]]; then
    echo "→ Skipping system dependencies (AI_CONTROLLER_SKIP_DEPS=1)"
else
    echo "→ Installing system packages (you may be asked for sudo password)..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq \
        python3 python3-venv python3-pip python3-dev \
        libgirepository1.0-dev libcairo2-dev python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
        xdotool xclip curl antimicrox pulseaudio-utils mpv wget git libportaudio2 libnotify-bin || {
        echo "ERROR: failed to install system packages" >&2
        exit 1
    }
fi

# ── 3. COPY REPO TO INSTALL LOCATION ─────────────────────────────────────────
echo "→ Installing AI Controller to ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}"
# Use rsync if available, otherwise copy core files.
if command -v rsync &>/dev/null; then
    rsync -a --delete \
        --exclude='.git' \
        --exclude='__pycache__' \
        --exclude='deprecated' \
        --exclude='scripts/extra' \
        --exclude='scripts/install.sh' \
        --exclude='scripts/controller-detect.sh' \
        --exclude='scripts/push-to-talk.sh' \
        "${REPO_DIR}/" "${INSTALL_DIR}/"
else
    rm -rf "${INSTALL_DIR:?}/"{scripts,profiles,systemd,docs,voices,README.md,install.sh}
    cp -r "${REPO_DIR}/scripts" "${INSTALL_DIR}/"
    cp -r "${REPO_DIR}/profiles" "${INSTALL_DIR}/"
    cp -r "${REPO_DIR}/systemd" "${INSTALL_DIR}/"
    cp -r "${REPO_DIR}/docs" "${INSTALL_DIR}/" 2>/dev/null || true
    cp "${REPO_DIR}/README.md" "${INSTALL_DIR}/" 2>/dev/null || true
fi

# ── 4. PYTHON VENV + PIP DEPENDENCIES ────────────────────────────────────────
echo "→ Creating Python virtual environment..."
# --system-site-packages lets the venv use the distro's python3-gi/pygi packages
# so we don't have to build PyGObject from source on every install.
python3 -m venv --system-site-packages "${INSTALL_DIR}/.venv"
"${INSTALL_DIR}/.venv/bin/pip" install --quiet --upgrade pip
"${INSTALL_DIR}/.venv/bin/pip" install --quiet \
    httpx fastapi uvicorn pynput numpy scipy edge-tts

# ── 5. PROMPT FOR GROQ API KEY ───────────────────────────────────────────────
echo ""
mkdir -p "${CONFIG_DIR}"
CONFIG_FILE="${CONFIG_DIR}/config.env"

if [[ -f "${CONFIG_FILE}" ]]; then
    echo "Found existing config at ${CONFIG_FILE}"
fi

GROQ_KEY=""
if [[ -f "${CONFIG_FILE}" ]]; then
    # shellcheck source=/dev/null
    GROQ_KEY=$(set -a; source "${CONFIG_FILE}" 2>/dev/null; echo "${GROQ_API_KEY:-}")
fi

# Allow non-interactive installs and testing via environment variable.
GROQ_KEY="${GROQ_API_KEY:-${GROQ_KEY}}"

if [[ -z "${GROQ_KEY}" ]]; then
    read -rp "Enter your Groq API key (get one at https://console.groq.com/keys): " GROQ_KEY
fi

if [[ -z "${GROQ_KEY}" ]]; then
    echo ""
    echo "======================================"
    echo "  ⚠️  CRITICAL: NO GROQ API KEY PROVIDED"
    echo "======================================"
    echo "Voice dictation will NOT work without a Groq API key."
    echo "Get a free key at: https://console.groq.com/keys"
    echo "Then add it to ${CONFIG_FILE}:"
    echo "  GROQ_API_KEY=your_key_here"
    echo ""
    echo "Would you like to enter it now? [Y/n]"
    read -rp "> " retry_key
    if [[ ! "$retry_key" =~ ^[Nn]$ ]]; then
        read -rp "Enter your Groq API key: " GROQ_KEY
    fi
fi

if [[ -z "${GROQ_KEY}" ]]; then
    echo "ERROR: No Groq API key provided. STT will not work." >&2
    echo "       Get a free key at https://console.groq.com/keys" >&2
    echo "       Then re-run install or add GROQ_API_KEY=... to ${CONFIG_FILE}" >&2
fi

# ── 6. WRITE CONFIG ──────────────────────────────────────────────────────────
cat > "${CONFIG_FILE}" <<EOF
# AI Controller configuration
AI_CONTROLLER_DIR=${INSTALL_DIR}
GROQ_API_KEY=${GROQ_KEY}
# Optional: override default PulseAudio devices
# AUDIO_INPUT=alsa_input.usb-Microsoft_Controller_....mono-fallback
# AUDIO_OUTPUT=alsa_output.usb-Microsoft_Controller_....stereo-fallback
EOF
chmod 600 "${CONFIG_FILE}"

# ── 7. INSTALL ANTIDOTE PROFILES ─────────────────────────────────────────────
# NOTE: the layout consolidated to a single general-purpose profile — every
# mode (desktop/browser/IPTV/YouTube TV) points at the same .amgp file, per
# controller-profile-switcher.sh. The old per-mode ai-*.amgp filenames this
# installer used to expect no longer exist in the repo.
echo "→ Installing AntiMicroX profiles..."
mkdir -p "${ANTIMICROX_PROFILE_DIR}"
cp "${INSTALL_DIR}/profiles/dont delete .gamecontroller.amgp" "${ANTIMICROX_PROFILE_DIR}/"
# Replace __AI_CONTROLLER_DIR__ placeholder with the actual install path.
sed -i "s|__AI_CONTROLLER_DIR__|${INSTALL_DIR}|g" "${ANTIMICROX_PROFILE_DIR}/dont delete .gamecontroller.amgp"

# ── 8. XONE DRIVER ENFORCEMENT + UDEV RULES ───────────────────────────────────
# The in-kernel xpad driver steals Xbox Series X/S controllers from xone and
# breaks both headset audio and input events. We hard-block xpad and install a
# boot-time guard. This modifies /etc/modprobe.d and runs update-initramfs,
# so we ask for consent first.

echo ""
echo "── Driver Enforcement ──"
echo "The in-kernel xpad driver conflicts with xone (needed for Xbox Series X/S"
echo "headset audio). The installer can blacklist xpad and install a boot-time"
echo "guard. This will run update-initramfs and modify system config."
echo ""
read -rp "Install xpad blacklist + xone driver guard? [y/N] " xone_consent

if [[ "$xone_consent" =~ ^[Yy]$ ]]; then
    echo "→ Enforcing xone-only Xbox controller driver (graphical password prompt)..."
    DISPLAY="${DISPLAY:-:0}" pkexec bash -c "
        cp '${REPO_DIR}/config/xone-blacklist.conf' /etc/modprobe.d/xone-blacklist.conf &&
        update-initramfs -u -k all &&
        install -m 755 '${INSTALL_DIR}/scripts/xone-driver-guard.sh' /usr/local/bin/xone-driver-guard.sh &&
        cp '${REPO_DIR}/systemd/xone-driver-guard.service' /etc/systemd/system/xone-driver-guard.service &&
        systemctl daemon-reload &&
        systemctl enable --now xone-driver-guard.service
    " || echo "WARNING: xone driver enforcement step failed — controller/headset may still use xpad" >&2
else
    echo "→ Skipped xone driver enforcement. You can install it later with:"
    echo "  sudo cp '${REPO_DIR}/config/xone-blacklist.conf' /etc/modprobe.d/xone-blacklist.conf"
    echo "  sudo update-initramfs -u -k all"
fi

# ── 8b. UDEV RULE + DEVICE ACCESS ─────────────────────────────────────────────
# AntiMicroX creates uinput devices that need per-user ACLs so systemd user
# services (ptt-pynput) can read/write them. Without the uaccess tag + input
# group membership, evdev.list_devices() silently drops them and PTT breaks.
echo "→ Installing udev rule + device access for antimicrox virtual devices..."
DISPLAY="${DISPLAY:-:0}" pkexec bash -c "
    cp '${REPO_DIR}/udev/90-antimicrox.rules' /etc/udev/rules.d/90-antimicrox.rules &&
    usermod -aG input '$(whoami)' &&
    udevadm control --reload-rules &&
    udevadm trigger --action=add --subsystem-match=input
" || echo "WARNING: udev rule install failed — PTT may not see antimicrox devices" >&2

# ── 10. INSTALL SYSTEMD SERVICES ──────────────────────────────────────────────
echo "→ Installing systemd user services..."
mkdir -p "${SERVICE_DIR}"
for svc in "${SERVICES[@]}"; do
    cp "${INSTALL_DIR}/systemd/${svc}" "${SERVICE_DIR}/"
done

systemctl --user daemon-reload
# Services are enabled (not launcher-controlled) so a reboot/logout doesn't
# silently kill the rig with no auto-recovery — see the 2026-07-12 fix note.
for svc in "${SERVICES[@]}"; do
    systemctl --user enable --now "${svc}" 2>/dev/null || true
done

# ── 11. INSTALL DESKTOP LAUNCHER (no autostart — launcher app controls services) ──
echo "→ Installing desktop launcher..."
chmod +x "${INSTALL_DIR}/scripts/ai-controller-launcher.sh"
DESKTOP_DIR="${HOME}/.local/share/applications"
mkdir -p "${DESKTOP_DIR}"
cp "${INSTALL_DIR}/ai-controller-launcher.desktop" "${DESKTOP_DIR}/"
sed -i "s|__AI_CONTROLLER_DIR__|${INSTALL_DIR}|g" "${DESKTOP_DIR}/ai-controller-launcher.desktop"

# ── 12. NOTE: Services are NOT auto-started — use the AI Controller launcher app.
echo ""
echo "→ Services installed but not started. Use the AI Controller desktop app to start them."
echo ""
echo "Driver enforcement:"
echo "  xpad is hard-blocked; xone-only is enforced at boot by xone-driver-guard.service"
echo ""
echo "Check driver status with:"
echo "  lsmod | grep -E 'xpad|xone_wired'"
echo "  systemctl status xone-driver-guard.service"
echo ""

echo ""
echo "======================================"
echo "  INSTALLATION COMPLETE"
echo "======================================"
echo ""
echo "Install directory: ${INSTALL_DIR}"
echo "Config file:       ${CONFIG_FILE}"
echo ""
echo "Check status with:"
echo "  systemctl --user status ${SERVICES[*]}"
echo ""
echo "The AI Controller launcher is in your applications menu. Click it to start/stop services."
echo "Plug in your controller, put on headphones, and press Right Trigger to talk."
