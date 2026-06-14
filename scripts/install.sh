#!/usr/bin/env bash
# ai-controller-profile installer
# Supports: Ubuntu/Debian/Mint Linux, macOS (partial), Windows (manual)
# Controller: Xbox One/Series (also PS4/PS5)
# Usage: bash install.sh

set -e

PROFILE_DIR="$HOME/.config/antimicrox"
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ANTIMICROX_APP="$HOME/scripts/antimicrox.AppImage"

echo "======================================"
echo "  AI Controller Profile — Installer"
echo "======================================"
echo ""

# ── 1. DETECT OS ──────────────────────────────────────────────────────────────
OS="unknown"
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    OS="linux"
elif [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macos"
    echo "macOS: partial support. antimicroX not available. Using Joystick Doctor or Controlly instead."
elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
    OS="windows"
    echo "Windows: Install antimicroX from https://github.com/AntiMicroX/antimicrox/releases"
    echo "Then copy profiles from profiles/ to %APPDATA%/antimicrox/"
    exit 0
fi

# ── 2. INSTALL ANTIMICROX (Linux only) ───────────────────────────────────────
if [[ "$OS" == "linux" ]]; then
    if command -v antimicrox &>/dev/null; then
        echo "✓ antimicroX already installed: $(which antimicrox)"
    elif [[ -f "$ANTIMICROX_APP" ]]; then
        echo "✓ antimicroX AppImage found at $ANTIMICROX_APP"
        # Create desktop entry
        mkdir -p "$HOME/.local/share/applications"
        cat > "$HOME/.local/share/applications/antimicrox.desktop" << EOF
[Desktop Entry]
Name=AntiMicroX
Exec=$ANTIMICROX_APP
Type=Application
Categories=Utility;
EOF
    else
        echo "→ Downloading antimicroX AppImage..."
        mkdir -p "$HOME/scripts"
        wget -q --show-progress \
            "https://github.com/AntiMicroX/antimicrox/releases/download/3.5.1/antimicrox-x86_64.AppImage" \
            -O "$HOME/scripts/antimicrox.AppImage"
        chmod +x "$HOME/scripts/antimicrox.AppImage"
        echo "✓ Downloaded antimicroX to ~/scripts/antimicrox.AppImage"
    fi
fi

# ── 3. INSTALL PROFILES ───────────────────────────────────────────────────────
echo ""
echo "→ Installing controller profiles..."
mkdir -p "$PROFILE_DIR"
cp "$SCRIPT_DIR/profiles/desktop.gamepad"  "$PROFILE_DIR/ai-desktop.amgp"
cp "$SCRIPT_DIR/profiles/browser.gamepad"  "$PROFILE_DIR/ai-browser.amgp"
cp "$SCRIPT_DIR/profiles/iptv.gamepad"     "$PROFILE_DIR/ai-iptv.amgp"
echo "✓ Profiles installed to $PROFILE_DIR"

# ── 4. INSTALL AUTO-DETECT SERVICE ───────────────────────────────────────────
echo ""
echo "→ Installing auto-detect service..."
cp "$SCRIPT_DIR/scripts/controller-detect.sh" "$HOME/scripts/controller-detect.sh"
chmod +x "$HOME/scripts/controller-detect.sh"

mkdir -p "$HOME/.config/systemd/user"
cp "$SCRIPT_DIR/systemd/antimicrox-autoload.service" "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable antimicrox-autoload.service
systemctl --user start antimicrox-autoload.service
echo "✓ Auto-detect service enabled and started"

# ── 5. PUSH-TO-TALK SETUP ─────────────────────────────────────────────────────
echo ""
echo "→ Installing push-to-talk script..."
cp "$SCRIPT_DIR/scripts/push-to-talk.sh" "$HOME/scripts/push-to-talk.sh"
chmod +x "$HOME/scripts/push-to-talk.sh"

echo ""
echo "======================================"
echo "  INSTALLATION COMPLETE"
echo "======================================"
echo ""
echo "  PROFILES INSTALLED:"
echo "  • Desktop    → ~/scripts/antimicrox.AppImage --profile ai-desktop"
echo "  • Browser    → auto-activates on Chrome/Firefox focus"
echo "  • IPTV       → auto-activates on MPV/VLC/Kodi/Hypnotix focus"
echo ""
echo "  PUSH-TO-TALK:"
echo "  Right Trigger (RT) sends F13 key."
echo "  Bind F13 to your mic/voice software."
echo "  OR use your headphone's built-in mic button instead."
echo ""
echo "  START ANTIMICROX:"
echo "  ~/scripts/antimicrox.AppImage &"
echo ""
echo "  LAUNCH ON BOOT:"
echo "  systemctl --user status antimicrox-autoload.service"
echo ""
