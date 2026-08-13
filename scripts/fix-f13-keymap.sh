#!/bin/bash
# Re-apply the xmodmap F13 overlay and re-trigger udev ACLs.
#
# Use this when the right trigger stops working after:
#   - AntiMicroX restart
#   - Controller hotplug / USB reconnect
#   - X server keymap reload
#
# Usage: bash scripts/fix-f13-keymap.sh
set -euo pipefail

echo "→ Re-applying xmodmap F13 overlay..."
DISPLAY=:0 xmodmap -e "keycode 191 = F13" 2>/dev/null || echo "  WARN: xmodmap keycode 191 failed"
DISPLAY=:0 xmodmap -e "keycode 202 = F13" 2>/dev/null || echo "  WARN: xmodmap keycode 202 failed"

echo "→ Re-triggering udev ACLs for antimicrox devices..."
sudo udevadm trigger --action=add --subsystem-match=input 2>/dev/null || echo "  WARN: udevadm trigger failed (need sudo)"

echo "→ Restarting PTT service..."
systemctl --user restart ptt-pynput.service 2>/dev/null || echo "  WARN: ptt-pynput restart failed"

echo ""
echo "✓ F13 keymap overlay applied. Test the right trigger now."
echo "  Verify: DISPLAY=:0 xmodmap -pk | grep -E '^(191|202)'"