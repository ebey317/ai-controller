#!/bin/bash
# Fix Xbox controller driver if xone didn't bind (e.g. after reboot/USB reconnect).
# Now delegates to xone-driver-guard.sh, which enforces xone-only and restarts
# user services if it had to correct anything.
set -euo pipefail
if [[ -x /usr/local/bin/xone-driver-guard.sh ]]; then
    sudo /usr/local/bin/xone-driver-guard.sh
else
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    sudo "${SCRIPT_DIR}/xone-driver-guard.sh"
fi
