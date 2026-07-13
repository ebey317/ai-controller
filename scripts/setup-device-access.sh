#!/bin/bash
# One-time sudo setup for AI Controller device access.
# Run this once: bash ~/ai-controller/scripts/setup-device-access.sh
set -e

echo "AI Controller — Device Access Setup"
echo "===================================="
echo "This script needs sudo to:"
echo "  1. Install udev rule for antimicrox virtual devices"
echo "  2. Add your user to the input group"
echo "  3. Reload udev rules and trigger"
echo ""

# 1. Install udev rule
echo "→ Installing udev rule..."
sudo cp ~/ai-controller/udev/90-antimicrox.rules /etc/udev/rules.d/
sudo chown root:root /etc/udev/rules.d/90-antimicrox.rules

# 2. Add user to input group (belt-and-suspenders with udev uaccess)
echo "→ Adding $(whoami) to input group..."
sudo usermod -aG input "$(whoami)"

# 3. Reload and trigger
echo "→ Reloading udev rules..."
sudo udevadm control --reload-rules
sudo udevadm trigger --action=add --subsystem-match=input

echo ""
echo "✓ Done. Log out and back in for the input group change to take effect."
echo "  (Or run: su - \$USER -c 'groups' to verify)"
echo ""
echo "After re-login, the F13 pipeline is fully self-healing:"
echo "  - antimicrox restart → xmodmap re-applied automatically (ExecStartPost)"
echo "  - controller hotplug → xmodmap re-applied by profile-switcher watch loop"
echo "  - device access → udev rule + input group membership"