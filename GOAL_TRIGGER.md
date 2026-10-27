# Ctrl+F13 Goal Trigger

Hold **Ctrl** and press/release **F13** (AntiMicroX maps controller L+R to `Control+F13`) to capture a voice goal instead of typing it.

- Plain **F13** alone keeps the existing dictation/type path unchanged.
- The goal fork is behind the env flag `TAHUTI_GOALS=1` and is **default OFF**.
- When enabled, the existing capture/transcribe pipeline runs, then the transcript is written to `~/.tahuti/goals/<uuid>.json` for the Tahuti headless runner.

## Enabling

Export `TAHUTI_GOALS=1` in the environment before launching `scripts/ptt_pynput.py`.
