# Voice Profile Manager — Design Spec

## Goal

A single, consumer-ready command-line tool that lets a user manage every aspect
of the AI Controller's TTS voice profiles without editing JSON or touching
files directly.

## Scope

The manager will support:

1. **Inspect** — list installed packs, see active voice, lock/unlock status,
   engine, and description.
2. **Switch** — set the active voice for the bridge and launcher.
3. **Install** — install a voice pack from a `.zip` or a downloaded Piper
   `.onnx` + `.onnx.json` pair.
4. **Remove** — delete a voice pack cleanly.
5. **Unlock** — mark a premium pack as purchased (unlocks it for use).
6. **Create** — generate a new `config.json` for a local Piper model or an
   Edge-TTS cloud voice.
7. **Preview** — speak sample text through any unlocked voice without changing
   the active profile.
8. **Tune** — adjust per-voice pitch, rate, label, and description and persist
   them in the pack's `config.json`.

Out of scope for this spec: a GUI editor (the launcher can call the CLI),
recording custom voice clones, or a marketplace downloader.

## Architecture

- **Entry point:** `scripts/voice_manager.py`
- **Reuses existing modules:**
  - `voice_toggle.py` for discovery, active-voice read/write, and unlock state.
  - `voice_bridge.py` for Edge-TTS synthesis path.
  - `ai_controller_paths.py` for install-root and config paths.
- **New shared helper (optional):** `voice_pack.py` dataclass/helper functions
  for loading/saving `config.json` so both `voice_toggle.py` and
  `voice_manager.py` share one schema.

## Data Model

Each pack is a folder under `voices/<voice_id>/` containing:

```json
{
  "name": "Aria",
  "label": "Aria",
  "engine": "piper",
  "model": "en_US-kristin-medium.onnx",
  "voice": "en-US-AriaNeural",
  "pitch": "+0Hz",
  "rate": "+0%",
  "locked": false,
  "price": "",
  "description": "Female local neural voice via Piper"
}
```

- `engine`: `"piper"` or `"edge-tts"`.
- `model`: required for Piper, ignored for Edge-TTS.
- `voice`: required for Edge-TTS, ignored for Piper.
- `pitch`/`rate`: optional, used by Edge-TTS today; stored for Piper for future
  use.
- `locked`: premium placeholder packs ship `true` without a model.
- `price`: human-readable price string shown in listings.

State files (already in use):
- `~/.config/ai_controller_voice` — active voice ID.
- `~/.config/ai_controller_unlocked_voices.json` — list of unlocked premium IDs.

## Commands

| Command | Purpose |
|---------|---------|
| `voice_manager.py list` | Show all packs, active marker, lock status |
| `voice_manager.py set <id>` | Make a pack the active voice |
| `voice_manager.py install <zip>` | Unzip a pack into `voices/` |
| `voice_manager.py install-model <id> <onnx> [json]` | Add a model to a locked pack and unlock it |
| `voice_manager.py remove <id>` | Delete a pack |
| `voice_manager.py unlock <id>` | Mark a locked pack as purchased |
| `voice_manager.py create-piper <id> <onnx>` | Create a new local Piper pack |
| `voice_manager.py create-edge <id> <voice-name>` | Create a new Edge-TTS pack |
| `voice_manager.py preview <id> [text]` | Speak sample text through a voice |
| `voice_manager.py tune <id> --pitch ... --rate ... --label ...` | Edit metadata and playback settings |

## Error Handling

- Missing or malformed `config.json` is reported with the pack ID and skipped.
- A locked pack cannot be set active or previewed until unlocked.
- Piper packs without a working `.onnx` are listed as "incomplete".
- Edge-TTS preview requires network; fallback message on failure.

## Testing

- Manual validation with existing packs: `voice_manager.py list`
- Preview each voice: `voice_manager.py preview joe`, `voice_manager.py preview aria`
- Create a test Edge pack, preview, then remove.
- Verify active voice changes are reflected in `voice_toggle.py load_voice()`.

## Future Work

- Add a GTK page in `ai-controller-launcher.py` that calls `voice_manager.py`
  for GUI users.
- Add a `--marketplace` flag to download packs from a URL (after Gumroad/store
  integration).
