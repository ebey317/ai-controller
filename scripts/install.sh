#!/usr/bin/env bash
# AI Controller — Consumer installer
# Run: bash install.sh
# Sets up a standalone, reboot-safe AI Controller on Linux, macOS, and Windows (WSL).

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${HOME}/ai-controller"
CONFIG_DIR="${HOME}/.config/ai-controller"
SERVICE_DIR="${HOME}/.config/systemd/user"
ANTIMICROX_PROFILE_DIR="${HOME}/.config/antimicrox"

SERVICES=(
    antimicrox-autoload.service
    controller-legend.service
    ptt-pynput.service
    voice-bridge.service
    ai-slide-keyboard.service
)

PIPER_VOICE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/joe/medium/en_US-joe-medium.onnx"
PIPER_CONFIG_URL="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/joe/medium/en_US-joe-medium.onnx.json"

echo "======================================"
echo "  AI Controller Installer"
echo "======================================"
echo ""

# ── 1. OS CHECK ──────────────────────────────────────────────────────────────
# Detect platform and set appropriate package manager
PLATFORM="unknown"
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    PLATFORM="linux"
    echo "→ Platform: Linux (Ubuntu/Mint/Debian)"
elif [[ "$OSTYPE" == "darwin"* ]]; then
    PLATFORM="macos"
    echo "→ Platform: macOS"
elif [[ "$OSTYPE" == "msys"* ]] || [[ "$OSTYPE" == "cygwin"* ]]; then
    PLATFORM="windows"
    echo "→ Platform: Windows (WSL/MSYS)"
else
    echo "⚠️  Unknown platform: $OSTYPE"
    echo "   Continuing with best-effort installation..."
    PLATFORM="unknown"
fi

# ── 2. INSTALL SYSTEM DEPENDENCIES ───────────────────────────────────────────
if [[ "${AI_CONTROLLER_SKIP_DEPS:-}" == "1" ]]; then
    echo "→ Skipping system dependencies (AI_CONTROLLER_SKIP_DEPS=1)"
elif [[ "$PLATFORM" == "linux" ]]; then
    echo "→ Installing system packages (you may be asked for sudo password)..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq \
        python3 python3-venv python3-pip python3-dev \
        libgirepository1.0-dev libcairo2-dev python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
        xdotool xclip curl antimicrox pulseaudio-utils mpv wget git libportaudio2 libnotify-bin || {
        echo "ERROR: failed to install system packages" >&2
        exit 1
    }
elif [[ "$PLATFORM" == "macos" ]]; then
    echo "→ Installing system packages via Homebrew..."
    if ! command -v brew &>/dev/null; then
        echo "ERROR: Homebrew not found. Install from https://brew.sh first." >&2
        exit 1
    fi
    brew install python3 gtk+3 cairo gobject-introspection pygobject3 \
        xdotool xclip curl antimicrox mpv wget git portaudio || {
        echo "ERROR: failed to install system packages via brew" >&2
        exit 1
    }
elif [[ "$PLATFORM" == "windows" ]]; then
    echo "→ Windows/WSL detected. Installing WSL-specific packages..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq \
        python3 python3-venv python3-pip python3-dev \
        libgirepository1.0-dev libcairo2-dev python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
        xdotool xclip curl antimicrox pulseaudio-utils mpv wget git libportaudio2 libnotify-bin || {
        echo "ERROR: failed to install WSL system packages" >&2
        exit 1
    }
else
    echo "→ Platform: unknown — attempting generic Python install..."
    echo "   You may need to install GTK3, Cairo, and PyGObject manually."
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
    httpx fastapi uvicorn pynput numpy scipy piper-tts edge-tts

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
    echo "WARNING: No Groq API key provided. STT will not work until you add one to ${CONFIG_FILE}" >&2
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

# ── 7. INSTALL ANTIDOTE PROFILES ─────────────────────────────────────────────
echo "→ Installing AntiMicroX profiles..."
mkdir -p "${ANTIMICROX_PROFILE_DIR}"
cp "${INSTALL_DIR}/profiles/ai-desktop.amgp" "${ANTIMICROX_PROFILE_DIR}/"
cp "${INSTALL_DIR}/profiles/ai-browser.amgp" "${ANTIMICROX_PROFILE_DIR}/" 2>/dev/null || true
cp "${INSTALL_DIR}/profiles/ai-iptv.amgp" "${ANTIMICROX_PROFILE_DIR}/" 2>/dev/null || true
cp "${INSTALL_DIR}/profiles/ai-youtube-tv.amgp" "${ANTIMICROX_PROFILE_DIR}/" 2>/dev/null || true

# Substitute placeholder paths with the real install directory.
sed -i "s|__AI_CONTROLLER_DIR__|${INSTALL_DIR}|g" "${ANTIMICROX_PROFILE_DIR}/ai-desktop.amgp"

# ── 8. DOWNLOAD DEFAULT PIPER VOICE ──────────────────────────────────────────
echo "→ Downloading default Piper voice (Joe)..."
mkdir -p "${INSTALL_DIR}/voices/joe"
wget -q --show-progress -O "${INSTALL_DIR}/voices/joe/en_US-joe-medium.onnx" "${PIPER_VOICE_URL}" || true
wget -q --show-progress -O "${INSTALL_DIR}/voices/joe/en_US-joe-medium.onnx.json" "${PIPER_CONFIG_URL}" || true
# The repo already ships voices/joe/config.json; ensure it exists in the install.
if [[ ! -f "${INSTALL_DIR}/voices/joe/config.json" ]]; then
    cp "${REPO_DIR}/voices/joe/config.json" "${INSTALL_DIR}/voices/joe/config.json"
fi

# ── 9. INSTALL SYSTEMD SERVICES ──────────────────────────────────────────────
echo "→ Installing systemd user services..."
mkdir -p "${SERVICE_DIR}"
for svc in "${SERVICES[@]}"; do
    cp "${INSTALL_DIR}/systemd/${svc}" "${SERVICE_DIR}/"
done

systemctl --user daemon-reload
for svc in "${SERVICES[@]}"; do
    systemctl --user enable "${svc}"
done

# ── 10. INSTALL DESKTOP LAUNCHER & AUTOSTART ─────────────────────────────────
echo "→ Installing desktop launcher..."
chmod +x "${INSTALL_DIR}/scripts/ai-controller-launcher.sh"
DESKTOP_DIR="${HOME}/.local/share/applications"
AUTOSTART_DIR="${HOME}/.config/autostart"
mkdir -p "${DESKTOP_DIR}" "${AUTOSTART_DIR}"
cp "${INSTALL_DIR}/ai-controller-launcher.desktop" "${DESKTOP_DIR}/"
cp "${INSTALL_DIR}/ai-controller-launcher.desktop" "${AUTOSTART_DIR}/"
sed -i "s|__AI_CONTROLLER_DIR__|${INSTALL_DIR}|g" "${DESKTOP_DIR}/ai-controller-launcher.desktop"
sed -i "s|__AI_CONTROLLER_DIR__|${INSTALL_DIR}|g" "${AUTOSTART_DIR}/ai-controller-launcher.desktop"

# ── 11. START SERVICES ───────────────────────────────────────────────────────
echo ""
echo "→ Starting services..."
for svc in "${SERVICES[@]}"; do
    systemctl --user restart "${svc}" || true
done

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
echo "The AI Controller launcher is in your applications menu and will autostart on login."
echo "Plug in your controller, put on headphones, and press Right Trigger to talk."
