# Ctrl+F13 Goal Trigger

Hold **Ctrl** and press/release **F13** (AntiMicroX maps controller L+R to `Control+F13`) to capture a voice goal instead of typing it. This "agent take" is **always enabled** — no env flag required.

- Plain **F13** alone keeps the existing dictation/type path unchanged.
- **Agent take (Ctrl+F13):** writes the transcript to `~/.tahuti/goals/<uuid>.json` for the Tahuti headless runner, gets a spoken response, and shows the avatar.
- **Legacy flag:** `TAHUTI_GOALS=1` is a legacy alias for the same agent-take behavior; it is no longer required.

## Enabling

No configuration needed — the Ctrl+F13 trigger works out of the box. The legacy `TAHUTI_GOALS=1` env var is still accepted but not required.
