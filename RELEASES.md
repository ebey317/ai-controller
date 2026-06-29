# Releasing & Auto-Updates

This document explains how to ship AI Controller to buyers and keep them updated.

## The model

- **Private dev repo**: `ebey317/-AI-controller.` (this repo) — where you develop.
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
3. Set the buyer's update URL:
   ```bash
   echo 'https://raw.githubusercontent.com/ebey317/ai-controller-releases/main' > ~/.config/ai-controller/update_url
   ```

### Option B: Cloud object storage (cheap, direct downloads)

- **Cloudflare R2** — no egress fees, S3-compatible.
- **AWS S3** — standard object storage.
- **Backblaze B2** — cheap storage, free egress up to a limit.

Upload `VERSION` and `ai-controller-latest.tar.gz` to a public bucket and point `~/.config/ai-controller/update_url` at the bucket URL.

## Building a release archive

```bash
cd /path/to/-AI-controller.
VERSION=$(cat VERSION)
tar --exclude='.git' --exclude='.github' -czf "ai-controller-${VERSION}.tar.gz" .
cp "ai-controller-${VERSION}.tar.gz" ai-controller-latest.tar.gz
```

Then upload `VERSION` and `ai-controller-latest.tar.gz` to your public release host.

## Buyer update flow

After the buyer installs the archive, they run:

```bash
bash "$HOME/ai-controller/scripts/update.sh"
```

Or bind it to a controller button in AntiMicroX.

The script:
1. Reads `~/.config/ai_controller_update_url`.
2. Fetches the remote `VERSION` file.
3. Compares it to the local `VERSION`.
4. Downloads and extracts `ai-controller-latest.tar.gz` if newer.
5. Preserves buyer state (`ptt_mode`, vocabulary, voice unlocks).
6. Restarts systemd services.

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

1. Build `ai-controller-1.0.0.tar.gz`.
2. Upload it as the Gumroad product file.
3. Upload the same file as `ai-controller-latest.tar.gz` to your public release host.
4. When you ship an update, bump `VERSION`, rebuild, upload new `ai-controller-latest.tar.gz`, and buyers run `update.sh`.
