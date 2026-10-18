# Releasing & Auto-Updates

This document explains how to ship AI Controller to buyers and keep them updated.

## The model

- **Private dev repo**: `ebey317/ai-controller` (this repo) — where you develop.
- **Public release host**: A separate public place buyers can poll for updates.
- **Buyer archive**: A `.tar.gz` built from this repo, shipped via Gumroad/GitHub Release.

The buyer's copy includes `scripts/update.sh`, which checks the public release host for a newer `VERSION` file and downloads `ai-controller-latest.tar.gz` when available.

## Why not just make this repo public?

If this repo is public, anyone can clone it for free. That breaks the $30 paid-product model. Keep this repo private and publish only release archives to a public host.

## Recommended public release hosts

### Option A: Public GitHub releases repo (free)

1. Create a new public repo, e.g. `ebey317/ai-controller-releases`.
2. Push only these files to it:
   - `VERSION`
   - `ai-controller-latest.tar.gz`
   - `ai-controller-latest.tar.gz.sha256`
3. Set the buyer's update URL:
   ```bash
   echo 'https://raw.githubusercontent.com/ebey317/ai-controller-releases/main' > ~/.config/ai-controller/update_url
   ```

### Option B: Cloud object storage (cheap, direct downloads)

- **Cloudflare R2** — no egress fees, S3-compatible.
- **AWS S3** — standard object storage.
- **Backblaze B2** — cheap storage, free egress up to a limit.

Upload `VERSION`, `ai-controller-latest.tar.gz`, and `ai-controller-latest.tar.gz.sha256` to a public bucket and point `~/.config/ai-controller/update_url` at the bucket URL.

## Building a release archive

```bash
cd /path/to/ai-controller
VERSION=$(cat VERSION)
tar --exclude='.git' --exclude='.github' -czf "ai-controller-${VERSION}.tar.gz" .
cp "ai-controller-${VERSION}.tar.gz" ai-controller-latest.tar.gz
sha256sum ai-controller-latest.tar.gz > ai-controller-latest.tar.gz.sha256
```

Then upload `VERSION`, `ai-controller-latest.tar.gz`, and `ai-controller-latest.tar.gz.sha256` to your public release host. The `.sha256` file is required: `update.sh` will refuse to install a packaged update whose hash cannot be verified.

## Signing releases (required once the pinned key ships)

A checksum fetched from the same host does not stop an attacker who controls
that host. When `scripts/update_pubkey.pem` exists in the installed build,
`update.sh` requires and verifies a detached Ed25519 signature for every
release. One-time key setup (keep the private key offline, never on the
release host):

```bash
openssl genpkey -algorithm ed25519 -out release_priv.pem
openssl pkey -in release_priv.pem -pubout -out scripts/update_pubkey.pem
git add scripts/update_pubkey.pem   # the PUBLIC half ships with the product
```

Every release, after building the tarball:

```bash
openssl pkeyutl -sign -inkey release_priv.pem -rawin \
    -in ai-controller-latest.tar.gz -out ai-controller-latest.tar.gz.sig
```

Upload the `.sig` next to the tarball. To rotate the signing key: sign the
next release with the new key and ship its new `scripts/update_pubkey.pem`
inside that release (verification always uses the pubkey of the *installed*
build, so trust chains release-by-release).

Update URLs must be `https://` (or `file://` for local testing); plain HTTP
is refused unless `AI_CONTROLLER_ALLOW_INSECURE_URL=1` is set on a trusted
local network.

## Buyer update flow

After the buyer installs the archive, they run:

```bash
bash "$HOME/ai-controller/scripts/update.sh"
```

Or bind it to a controller button in AntiMicroX.

The script:
1. Reads `~/.config/ai-controller/update_url` (https required).
2. Fetches the remote `VERSION` file and compares it to the local `VERSION`.
3. Downloads `ai-controller-latest.tar.gz` plus its `.sha256` manifest.
4. Verifies the tarball's SHA256 hash before extracting. If the manifest is missing or the hash does not match, the update aborts.
5. If `scripts/update_pubkey.pem` is present, verifies the release's Ed25519 `.sig`. Missing or invalid signatures abort.
6. Refuses archives containing absolute or `..` path members (path traversal).
7. Preserves buyer state (`ptt_mode`, vocabulary, voice unlocks).
8. Restarts systemd services.

## Versioning

Use semantic versioning in the `VERSION` file:

```
1.0.0
```

Bump appropriately:
- **Major** — breaking changes
- **Minor** — new features, new voice packs, new modes
- **Patch** — bug fixes

## Gumroad workflow

1. Build `ai-controller-1.0.0.tar.gz` and `ai-controller-latest.tar.gz.sha256`.
2. Upload the archive as the Gumroad product file.
3. Upload the same archive and its `.sha256` file as `ai-controller-latest.tar.gz` and `ai-controller-latest.tar.gz.sha256` to your public release host.
4. When you ship an update, bump `VERSION`, rebuild, upload the new archive and checksum, and buyers run `update.sh`.
