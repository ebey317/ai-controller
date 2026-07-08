#!/bin/bash
# DEPRECATED — this script used to force-load xpad, which is exactly what broke
# xone audio/input. It now does the opposite: enforce xone-only.
set -euo pipefail
logger -t ai-controller "fix-xpad.sh is deprecated; redirecting to xone-driver-guard.sh"
if [[ -x /usr/local/bin/xone-driver-guard.sh ]]; then
    sudo /usr/local/bin/xone-driver-guard.sh
else
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    sudo "${SCRIPT_DIR}/xone-driver-guard.sh"
fi
