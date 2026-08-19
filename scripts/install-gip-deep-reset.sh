#!/usr/bin/env bash
# One-time installer (run with sudo): puts gip-deep-reset.sh in a root-owned
# location and adds a NOPASSWD sudoers rule so the gip-audio-watchdog can
# escalate to a kernel-level module reload without a password prompt.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${SCRIPT_DIR}/gip-deep-reset.sh"
DST="/usr/local/bin/gip-deep-reset.sh"
SUDOERS="/etc/sudoers.d/gip-deep-reset"

[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }

# The invoking (non-root) user — sudo sets SUDO_USER; fall back to the
# owner of this checkout for direct-root runs.
TARGET_USER="${SUDO_USER:-$(stat -c '%U' "$SCRIPT_DIR")}"

# Root-owned copy: sudoers must never point at a user-writable file.
install -o root -g root -m 755 "$SRC" "$DST"

echo "$TARGET_USER ALL=(root) NOPASSWD: $DST" > "$SUDOERS"
chmod 440 "$SUDOERS"
visudo -c -f "$SUDOERS"

# Run it once right now to clear the current wedge.
"$DST"

echo "installed: $DST + sudoers rule OK — watchdog can now deep-reset without a password"
