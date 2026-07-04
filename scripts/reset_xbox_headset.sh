#!/bin/bash
# Reset the xone Xbox controller audio driver.
# Uses pkexec to prompt for the admin password in a graphical dialog.
set -euo pipefail

if ! command -v pkexec >/dev/null 2>&1; then
    echo "pkexec not found. Install policykit-1 or run manually:" >&2
    echo "  sudo modprobe -r xone-gip-headset xone-gip xone-bus xone-dongle" >&2
    echo "  sudo modprobe xone" >&2
    exit 1
fi

pkexec bash -c '
    modprobe -r xone-gip-headset xone-gip xone-bus xone-dongle 2>/dev/null || true
    sleep 1
    modprobe xone
'

echo "Xbox headset driver reset complete."
