#!/usr/bin/env bash
# AI Controller — Uninstall Script
# Run: bash uninstall.sh
# Completely removes AI Controller from your system.

set -euo pipefail

INSTALL_DIR="$HOME/ai-controller"
CONFIG_DIR="$HOME/.config/ai-controller"
SERVICE_DIR="$HOME/.config/systemd/user"
DESKTOP_DIR="$HOME/.local/share/applications"
AUTOSTART_DIR="$HOME/.config/autostart"
ANTIMICROX_DIR="$HOME/.config/antimicrox"

echo "======================================"
echo "  AI Controller — Uninstaller"
echo "======================================"
echo ""

# Confirm before proceeding
read -rp "This will completely remove AI Controller from your system. Continue? [y/N] " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
fi

echo "→ Stopping services..."
systemctl --user stop ai-slide-keyboard.service controller-legend.service ptt-pynput.service voice-bridge.service antimicrox-autoload.service f13-xmodmap-heal.service 2>/dev/null || true

echo "→ Disabling services..."
systemctl --user disable ai-slide-keyboard.service controller-legend.service ptt-pynput.service voice-bridge.service antimicrox-autoload.service f13-xmodmap-heal.service 2>/dev/null || true

echo "→ Removing service files..."
rm -f "$SERVICE_DIR"/ai-slide-keyboard.service
rm -f "$SERVICE_DIR"/controller-legend.service
rm -f "$SERVICE_DIR"/ptt-pynput.service
rm -f "$SERVICE_DIR"/voice-bridge.service
rm -f "$SERVICE_DIR"/antimicrox-autoload.service
rm -f "$SERVICE_DIR"/f13-xmodmap-heal.service

systemctl --user daemon-reload

echo "→ Removing installation directory..."
rm -rf "$INSTALL_DIR"

echo "→ Removing config directory..."
rm -rf "$CONFIG_DIR"

echo "→ Removing desktop launcher..."
rm -f "$DESKTOP_DIR"/ai-controller-launcher.desktop
rm -f "$AUTOSTART_DIR"/ai-controller-launcher.desktop

echo "→ Removing AntiMicroX profiles..."
rm -f "$ANTIMICROX_DIR"/good_1n.gamecontroller.amgp
rm -f "$ANTIMICROX_DIR"/*.amgp 2>/dev/null || true

echo "→ Removing voice memos..."
rm -rf "$HOME/voice-memos" 2>/dev/null || true

echo ""
echo "======================================"
echo "  UNINSTALL COMPLETE"
echo "======================================"
echo ""
echo "AI Controller has been removed from your system."
echo ""
echo "Optional cleanup (manual):"
echo "  - Remove xpad blacklist: sudo rm /etc/modprobe.d/xone-blacklist.conf && rebuild your initramfs"
echo "    (update-initramfs -u -k all on Debian/Ubuntu, dracut -f on Fedora/openSUSE, mkinitcpio -P on Arch)"
echo "  - Remove xone driver guard: sudo systemctl disable --now xone-driver-guard.service && sudo rm /etc/systemd/system/xone-driver-guard.service /usr/local/bin/xone-driver-guard.sh"
echo "  - Remove udev rule: sudo rm /etc/udev/rules.d/90-antimicrox.rules && sudo udevadm control --reload-rules"
echo "  - Remove GitHub repo: https://github.com/ebey317/ai-controller"
echo ""
echo "You may need to log out and back in for all changes to take effect."
echo ""
