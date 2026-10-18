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
#
# SECURITY: packaged updates REQUIRE a SHA256 checksum manifest served from
# the same update host. If the manifest is missing or the tarball hash does
# not match, the update is aborted.
#
# Signed releases: when a pinned public key ships next to this script
# (scripts/update_pubkey.pem), every release MUST also carry a detached
# Ed25519 signature at <archive>.sig. Signing a release (one command):
#   openssl pkeyutl -sign -inkey release_priv.pem -rawin \
#       -in ai-controller-latest.tar.gz -out ai-controller-latest.tar.gz.sig
# The matching public key is committed at scripts/update_pubkey.pem. openssl
# is already required by this project's install path, so this adds no new
# runtime dependency. Rotating the signing key ships with the next release's
# pubkey; the private key never goes on the release host.

set -euo pipefail

INSTALL_DIR="${AI_CONTROLLER_DIR:-$HOME/ai-controller}"
CONTROLLER_SERVICES=(voice-bridge.service ptt-pynput.service controller-legend.service antimicrox-autoload.service ai-slide-keyboard.service)

# Set to 1 to allow packaged updates without a checksum manifest.
# DO NOT enable this for builds you ship to other people.
AI_CONTROLLER_SKIP_CHECKSUM="${AI_CONTROLLER_SKIP_CHECKSUM:-0}"

# Download scratch dir. Declared at script scope on purpose: the EXIT trap
# must still see it after url_update() returns (a `local` is destroyed
# before the EXIT trap runs, which made cleanup fail with an unbound-
# variable error and leak the dir on every successful update).
TMP_DIR=""

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

    # Transport: HTTPS only (file:// allowed for local/admin testing) --
    # a plain-HTTP manifest + tarball is trivially MITM-able even with
    # checksums served from the same host.
    case "$UPDATE_URL" in
        https://*|file://*) : ;;
        http://*)
            if [ "${AI_CONTROLLER_ALLOW_INSECURE_URL:-0}" != "1" ]; then
                echo "ERROR: refusing plain-HTTP update URL: $UPDATE_URL"
                echo "Use https:// (or set AI_CONTROLLER_ALLOW_INSECURE_URL=1 on a trusted local network)."
                exit 1
            fi
            ;;
        *)
            echo "ERROR: unsupported update URL scheme: $UPDATE_URL"
            exit 1
            ;;
    esac

    local VERSION_URL="$UPDATE_URL/VERSION"
    local ARCHIVE_URL="$UPDATE_URL/ai-controller-latest.tar.gz"
    local CHECKSUM_URL="$ARCHIVE_URL.sha256"

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

    TMP_DIR=$(mktemp -d)
    trap 'rm -rf "${TMP_DIR:-}"' EXIT

    local ARCHIVE_FILE="$TMP_DIR/ai-controller-$REMOTE_VERSION.tar.gz"
    local CHECKSUM_FILE="$TMP_DIR/ai-controller-$REMOTE_VERSION.tar.gz.sha256"

    echo "Downloading $ARCHIVE_URL ..."
    curl -fsSL "$ARCHIVE_URL" -o "$ARCHIVE_FILE"

    echo "Downloading checksum manifest $CHECKSUM_URL ..."
    local checksum_ok=0
    if curl -fsSL "$CHECKSUM_URL" -o "$CHECKSUM_FILE" 2>/dev/null; then
        local expected_hash
        expected_hash=$(awk '{print $1}' "$CHECKSUM_FILE" | tr -d '[:space:]' | head -n1)
        if [ -n "$expected_hash" ]; then
            local actual_hash
            actual_hash=$(sha256sum "$ARCHIVE_FILE" | awk '{print $1}')
            if [ "$expected_hash" = "$actual_hash" ]; then
                echo "Checksum verified: $actual_hash"
                checksum_ok=1
            else
                echo "ERROR: checksum mismatch."
                echo "  Expected: $expected_hash"
                echo "  Actual:   $actual_hash"
                echo "  The tarball has NOT been extracted. Your install is unchanged."
                exit 1
            fi
        else
            echo "WARNING: checksum manifest is empty."
        fi
    else
        echo "WARNING: no checksum manifest found at $CHECKSUM_URL"
    fi

    if [ "$checksum_ok" = "0" ]; then
        if [ "$AI_CONTROLLER_SKIP_CHECKSUM" = "1" ]; then
            echo "WARNING: AI_CONTROLLER_SKIP_CHECKSUM=1 — continuing without checksum verification."
            echo "This is unsafe for builds distributed to other people."
        else
            echo "ERROR: cannot verify update integrity."
            echo "For a packaged update to install, the update server must provide"
            echo "a SHA256 checksum file at:"
            echo "  $CHECKSUM_URL"
            echo ""
            echo "To bypass this check on your own machine (NOT recommended for shipped"
            echo "builds), set AI_CONTROLLER_SKIP_CHECKSUM=1."
            echo "The tarball has NOT been extracted. Your install is unchanged."
            exit 1
        fi
    fi

    # Signed releases: required when a pinned public key ships with this
    # build. A checksum fetched from the same host does not stop an attacker
    # who controls that host; a signature does (the private key never leaves
    # the release manager). Ed25519 via openssl — no extra dependency.
    local UPDATE_PUBKEY="${AI_CONTROLLER_UPDATE_PUBKEY:-$(dirname "$0")/update_pubkey.pem}"
    local SIG_FILE="$TMP_DIR/ai-controller-$REMOTE_VERSION.tar.gz.sig"
    if [ -f "$UPDATE_PUBKEY" ]; then
        if ! command -v openssl >/dev/null 2>&1; then
            echo "ERROR: this build requires signed releases, but openssl is missing."
            echo "The tarball has NOT been extracted. Your install is unchanged."
            exit 1
        fi
        if ! curl -fsSL "$ARCHIVE_URL.sig" -o "$SIG_FILE" 2>/dev/null; then
            echo "ERROR: no release signature found at $ARCHIVE_URL.sig"
            echo "Signed releases are required by this build. Refusing update."
            echo "The tarball has NOT been extracted. Your install is unchanged."
            exit 1
        fi
        if openssl pkeyutl -verify -pubin -inkey "$UPDATE_PUBKEY" -rawin \
                -in "$ARCHIVE_FILE" -sigfile "$SIG_FILE" >/dev/null 2>&1; then
            echo "Signature verified."
        else
            echo "ERROR: release signature verification FAILED."
            echo "The tarball has NOT been extracted. Your install is unchanged."
            exit 1
        fi
    fi

    # Refuse archives containing absolute or '..' path members before any
    # extraction (path traversal is code execution outside INSTALL_DIR).
    # NOTE: grep runs without -q on purpose: with `set -o pipefail`, -q's
    # early exit can SIGPIPE the tar listing and turn a real match into a
    # pipeline failure, silently bypassing the guard.
    if tar -tzf "$ARCHIVE_FILE" | grep -E '(^/|(^|/)\.\.(/|$))' >/dev/null; then
        echo "ERROR: release archive contains unsafe paths. Refusing update."
        echo "The tarball has NOT been extracted. Your install is unchanged."
        exit 1
    fi

    echo "Installing update ..."
    local BACKUP_DIR
    BACKUP_DIR="$INSTALL_DIR.backup.$(date +%s)"
    local NEW_DIR="$INSTALL_DIR.new"
    rm -rf "$NEW_DIR"
    mkdir -p "$NEW_DIR"
    tar -xzf "$ARCHIVE_FILE" -C "$NEW_DIR" --strip-components=1

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
