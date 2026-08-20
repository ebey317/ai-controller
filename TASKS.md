# AI Controller — Task Queue

## In Progress / Maintenance

- [ ] Keep `AGENTS.md` and `TASKS.md` accurate as the repo evolves.
- [ ] Monitor ghost recordings after the recorder-readiness patch; collect evidence if they recur.
- [ ] Rotate or cap `logs/voice-bridge.log` growth (currently gitignored; check disk usage periodically).
- [ ] Add tests for `ptt_pynput.py` debounce/lock and `slide_keyboard.py` drag math.
- [ ] Clean up committed `.bak.*` files from history when convenient.

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
