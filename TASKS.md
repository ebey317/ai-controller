# AI Controller — Task Queue

## Release blockers / verification required

- [ ] Run a clean Linux Mint X11 install and capture installer logs.
- [ ] Verify wired Xbox Series X/S controls and headset input on actual hardware.
- [ ] Test push-to-talk rapid trigger press/release, recording cancellation, and lock contention.
- [ ] Test floating keyboard focus and drag behavior on multiple applications.
- [ ] Test signed packaged update, invalid signature rejection, interrupted update, and recovery.
- [ ] Confirm paid distribution/licensing terms; MIT permits redistribution.
- [ ] Verify CI on the release branch before merging.

## In Progress / Maintenance

- [ ] Keep `AGENTS.md` and `TASKS.md` accurate as the repo evolves.
- [ ] Monitor ghost recordings after the recorder-readiness patch; collect evidence if they recur.
- [ ] Rotate or cap `logs/voice-bridge.log` growth (currently gitignored; check disk usage periodically).
- [ ] Add tests for `ptt_pynput.py` debounce/lock and `slide_keyboard.py` drag math.

## Done (Core Product Shipped)

- [x] Push-to-talk voice dictation pipeline (`ptt_pynput.py` + `voice_bridge.py` + Groq Whisper)
- [x] Floating on-screen keyboard (`slide_keyboard.py`)
- [x] Controller legend HUD (`controller-legend.py`)
- [x] Auto profile switching (`controller-profile-switcher.sh` + `systemd/`)
- [x] TTS responses via edge-tts + mpv
- [x] Xbox wired controller support with xone driver
- [x] USB autosuspend / udev fixes and xpad driver guard
- [x] Dictation modes: PRO, BUBBLY, CASUAL, BOLD, BIG
- [x] TTS barge-in on Right Trigger press
- [x] Ghost-recording prevention (debounce + processing lock + recorder-readiness gate)
- [x] Public-repo portability pass (no hardcoded `/home/elijah` paths)
- [x] MIT license + README + install wizard
- [x] Removed tracked `.bak.*` files from git history (`.gitignore` already excludes them)
