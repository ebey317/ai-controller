#!/bin/bash
# update.sh — Check for AI Controller updates and install the latest release.
#
# The buyer's archive ships with this script. It polls a public release URL
# (configured in ~/.config/ai-controller/update_url) for a version file,
# downloads the matching archive if newer, and restarts services.
#
# Default release host: a public GitHub repo or any HTTPS file server.

set -euo pipefail

INSTALL_DIR="${AI_CONTROLLER_DIR:-$HOME/ai-controller}"
UPDATE_URL="${AI_CONTROLLER_UPDATE_URL:-}"
STATE_FILE="$HOME/.config/ai-controller/version"
UPDATE_URL_FILE="$HOME/.config/ai-controller/update_url"
CONFIG_DIR="$HOME/.config/ai-controller"

if [ -z "$UPDATE_URL" ] && [ -f "$UPDATE_URL_FILE" ]; then
    UPDATE_URL=$(tr -d '[:space:]' < "$UPDATE_URL_FILE")
fi

if [ -z "$UPDATE_URL" ]; then
    echo "No update URL configured."
    echo "Set AI_CONTROLLER_UPDATE_URL or write it to $UPDATE_URL_FILE"
    exit 1
fi

VERSION_URL="$UPDATE_URL/VERSION"
ARCHIVE_URL="$UPDATE_URL/ai-controller-latest.tar.gz"

echo "Checking for updates from $VERSION_URL ..."

LOCAL_VERSION="0.0.0"
if [ -f "$INSTALL_DIR/VERSION" ]; then
    LOCAL_VERSION=$(tr -d '[:space:]' < "$INSTALL_DIR/VERSION")
elif [ -f "$STATE_FILE" ]; then
    LOCAL_VERSION=$(tr -d '[:space:]' < "$STATE_FILE")
fi

REMOTE_VERSION=$(curl -fsSL "$VERSION_URL" | tr -d '[:space:]' || true)
if [ -z "$REMOTE_VERSION" ]; then
    echo "Could not fetch remote version. Update server may be down."
    exit 1
fi

echo "Local version: $LOCAL_VERSION"
echo "Remote version: $REMOTE_VERSION"

if [ "$LOCAL_VERSION" = "$REMOTE_VERSION" ]; then
    echo "Already up to date."
    exit 0
fi

# Download update to temp location
TMP_DIR=$(mktemp -d)
trap "rm -rf $TMP_DIR" EXIT

echo "Downloading $ARCHIVE_URL ..."
curl -fsSL "$ARCHIVE_URL" -o "$TMP_DIR/ai-controller-$REMOTE_VERSION.tar.gz"

echo "Installing update ..."
# Extract next to the current install, then atomically swap
BACKUP_DIR="$INSTALL_DIR.backup.$(date +%s)"
NEW_DIR="$INSTALL_DIR.new"
rm -rf "$NEW_DIR"
mkdir -p "$NEW_DIR"
tar -xzf "$TMP_DIR/ai-controller-$REMOTE_VERSION.tar.gz" -C "$NEW_DIR" --strip-components=1



mv "$INSTALL_DIR" "$BACKUP_DIR"
mv "$NEW_DIR" "$INSTALL_DIR"

echo "$REMOTE_VERSION" > "$STATE_FILE"

echo "Updated to $REMOTE_VERSION. Backup at $BACKUP_DIR"
echo "Restarting services ..."
systemctl --user restart voice-bridge.service ptt-pynput.service controller-legend.service antimicrox-autoload.service 2>/dev/null || true

echo "Done."
