#!/bin/bash
# update.sh — Update AI Controller to the latest version.
#
# Two install shapes are supported:
#   1. Git checkout (this machine's setup) — `git pull --ff-only` in
#      INSTALL_DIR, then restart the controller services. This is the path
#      used whenever INSTALL_DIR is a git repo with a remote.
#   2. Packaged/"buyer" install with no git repo — falls back to polling a
#      release URL (configured in ~/.config/ai-controller/update_url) for a
#      version file and downloading a matching tarball, same as before.
#
# This script only ever touches INSTALL_DIR (code/scripts/profiles shipped
# with the repo). It never writes to ~/.config/antimicrox/*.amgp — those are
# live, per-machine runtime state, not something an update should silently
# overwrite.

set -euo pipefail

INSTALL_DIR="${AI_CONTROLLER_DIR:-$HOME/ai-controller}"
CONTROLLER_SERVICES=(voice-bridge.service ptt-pynput.service controller-legend.service antimicrox-autoload.service ai-slide-keyboard.service)

git_update() {
    echo "Checking for updates in $INSTALL_DIR (git) ..."

    local dirty
    dirty=$(git -C "$INSTALL_DIR" status --porcelain --untracked-files=no)
    if [ -n "$dirty" ]; then
        echo "Local changes present — resolve or stash them first, then re-run:"
        echo "$dirty"
        exit 1
    fi

    git -C "$INSTALL_DIR" fetch --quiet origin
    local before after
    before=$(git -C "$INSTALL_DIR" rev-parse --short HEAD)
    local behind
    behind=$(git -C "$INSTALL_DIR" rev-list --count HEAD..origin/master 2>/dev/null || echo 0)

    if [ "$behind" = "0" ]; then
        echo "Already up to date ($before)."
        exit 0
    fi

    if ! git -C "$INSTALL_DIR" pull --ff-only; then
        echo "Update failed — local branch has diverged from origin. Not applied."
        exit 1
    fi

    after=$(git -C "$INSTALL_DIR" rev-parse --short HEAD)
    echo "Updated $before -> $after ($behind commit(s))."
    echo "Restarting services ..."
    systemctl --user restart "${CONTROLLER_SERVICES[@]}" 2>/dev/null || true
    echo "Done."
}

url_update() {
    local UPDATE_URL="${AI_CONTROLLER_UPDATE_URL:-}"
    local STATE_FILE="$HOME/.config/ai-controller/version"
    local UPDATE_URL_FILE="$HOME/.config/ai-controller/update_url"

    if [ -z "$UPDATE_URL" ] && [ -f "$UPDATE_URL_FILE" ]; then
        UPDATE_URL=$(tr -d '[:space:]' < "$UPDATE_URL_FILE")
    fi

    if [ -z "$UPDATE_URL" ]; then
        echo "No update URL configured."
        echo "Set AI_CONTROLLER_UPDATE_URL or write it to $UPDATE_URL_FILE"
        exit 1
    fi

    local VERSION_URL="$UPDATE_URL/VERSION"
    local ARCHIVE_URL="$UPDATE_URL/ai-controller-latest.tar.gz"

    echo "Checking for updates from $VERSION_URL ..."

    local LOCAL_VERSION="0.0.0"
    if [ -f "$INSTALL_DIR/VERSION" ]; then
        LOCAL_VERSION=$(tr -d '[:space:]' < "$INSTALL_DIR/VERSION")
    elif [ -f "$STATE_FILE" ]; then
        LOCAL_VERSION=$(tr -d '[:space:]' < "$STATE_FILE")
    fi

    local REMOTE_VERSION
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

    local TMP_DIR
    TMP_DIR=$(mktemp -d)
    trap "rm -rf $TMP_DIR" EXIT

    echo "Downloading $ARCHIVE_URL ..."
    curl -fsSL "$ARCHIVE_URL" -o "$TMP_DIR/ai-controller-$REMOTE_VERSION.tar.gz"

    echo "Installing update ..."
    local BACKUP_DIR="$INSTALL_DIR.backup.$(date +%s)"
    local NEW_DIR="$INSTALL_DIR.new"
    rm -rf "$NEW_DIR"
    mkdir -p "$NEW_DIR"
    tar -xzf "$TMP_DIR/ai-controller-$REMOTE_VERSION.tar.gz" -C "$NEW_DIR" --strip-components=1

    mv "$INSTALL_DIR" "$BACKUP_DIR"
    mv "$NEW_DIR" "$INSTALL_DIR"

    echo "$REMOTE_VERSION" > "$STATE_FILE"

    echo "Updated to $REMOTE_VERSION. Backup at $BACKUP_DIR"
    echo "Restarting services ..."
    systemctl --user restart "${CONTROLLER_SERVICES[@]}" 2>/dev/null || true
    echo "Done."
}

if [ -d "$INSTALL_DIR/.git" ] && git -C "$INSTALL_DIR" remote get-url origin >/dev/null 2>&1; then
    git_update
else
    url_update
fi
