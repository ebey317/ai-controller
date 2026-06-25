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

# ── 6. F13 KEYMAP PERSISTENCE (Linux only) ────────────────────────────────────
# RT emits keysym F13 for dictation, but F13 has no keycode in the default X11
# keymap, and desktop keyboard daemons (Cinnamon csd-keyboard, GNOME, etc.)
# wipe xmodmap changes shortly after login. Three layers make F13 survive:
#   (a) ~/.Xmodmap          — the keycode definitions (source of truth)
#   (b) ~/.xsessionrc       — load them at X session start
#   (c) autostart .desktop  — re-load AFTER the keyboard daemon resets the map
#   (d) ptt service ExecStartPre also re-applies it (installed in step 4)
if [[ "$OS" == "linux" ]]; then
    echo ""
    echo "→ Installing F13-F18 keymap persistence..."

    # (a) keycode definitions — only write if ours aren't already present
    if [[ ! -f "$HOME/.Xmodmap" ]] || ! grep -q "keycode 202 = F13" "$HOME/.Xmodmap" 2>/dev/null; then
        cat >> "$HOME/.Xmodmap" << 'EOF'
! F13-F18 keycodes for AI Controller (PTT + onboard keyboard scanner)
! These survive restart when loaded via .xsessionrc + autostart + ptt service.
keycode 191 = F13
keycode 202 = F13
keycode 197 = F14
keycode 217 = F15
keycode 219 = F16
keycode 222 = F17
keycode 230 = F18
EOF
    fi

    # (b) load at X session start (idempotent)
    if ! grep -q "xmodmap ~/.Xmodmap" "$HOME/.xsessionrc" 2>/dev/null; then
        echo '[ -f ~/.Xmodmap ] && xmodmap ~/.Xmodmap' >> "$HOME/.xsessionrc"
    fi

    # (c) re-apply after the desktop keyboard daemon resets the keymap
    mkdir -p "$HOME/.config/autostart"
    cat > "$HOME/.config/autostart/fix-f13-keymap.desktop" << 'EOF'
[Desktop Entry]
Name=Fix F13 Keymap
Comment=Re-apply ~/.Xmodmap after the keyboard daemon resets the keymap, so RT->F13 dictation survives login
Exec=bash -c 'sleep 5; xmodmap ~/.Xmodmap'
Type=Application
Terminal=false
X-GNOME-Autostart-enabled=true
NoDisplay=true
Categories=Utility;Accessibility;
EOF

    # apply to the running session immediately
    [ -n "$DISPLAY" ] && xmodmap "$HOME/.Xmodmap" 2>/dev/null || true
    echo "✓ F13 keymap will persist across login + service restart"
fi

# ── 7. XBOX CONTROLLER HEADSET AUDIO DRIVER (Linux only) ──────────────────────
# Dictation mic + headset audio comes through the controller's 3.5mm jack,
# exposed by the xone-gip-headset kernel module. It must load at boot and must
# NOT be blacklisted. (xpad / mt76x2u stay blacklisted — they conflict.)
if [[ "$OS" == "linux" ]] && command -v sudo &>/dev/null; then
    echo ""
    echo "→ Configuring Xbox controller headset audio driver (needs sudo)..."

    # load at boot
    echo "xone-gip-headset" | sudo tee /etc/modules-load.d/xone-headset.conf >/dev/null

    # neutralize any blacklist of the headset module (preserve other blacklists)
    for f in /etc/modprobe.d/*.conf; do
        if grep -q "blacklist[[:space:]]\+xone_gip_headset" "$f" 2>/dev/null; then
            sudo sed -i 's/^[[:space:]]*blacklist[[:space:]]\+xone_gip_headset/# &  (disabled by ai-controller installer: needed for headset audio)/' "$f"
            echo "  ✓ removed headset blacklist in $(basename "$f")"
        fi
    done

    # load it now
    sudo modprobe xone-gip-headset 2>/dev/null || true
    if lsmod | grep -q xone_gip_headset; then
        echo "✓ Headset audio driver loaded and set to load at boot"
    else
        echo "  ! xone-gip-headset not loaded — is the xone DKMS package installed?"
    fi
fi

# ── 8. HEADSET MIC AUTO-WAKE (Linux only) ─────────────────────────────────────
# The controller announces its headset over GIP only on an analog insertion
# edge, so on boot/reconnect with the plug already seated the mic stays dead
# until a manual 3.5mm reseat. This installs a udev-triggered service that
# performs that reseat in software on connect — no plug-pull needed.
if [[ "$OS" == "linux" ]] && command -v sudo &>/dev/null; then
    echo ""
    echo "→ Installing headset mic auto-wake (needs sudo)..."
    sudo install -m 0755 "$SCRIPT_DIR/scripts/xbox-headset-wake.sh" /usr/local/bin/xbox-headset-wake.sh
    sudo install -m 0644 "$SCRIPT_DIR/systemd/xbox-headset-wake.service" /etc/systemd/system/xbox-headset-wake.service
    sudo install -m 0644 "$SCRIPT_DIR/udev/52-xbox-headset-wake.rules" /etc/udev/rules.d/52-xbox-headset-wake.rules
    sudo systemctl daemon-reload
    sudo udevadm control --reload-rules
    echo "✓ Headset mic will auto-wake on controller connect (no manual reseat)"
fi

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
