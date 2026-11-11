# AI Controller — Task Queue

## In Progress / Maintenance

- [ ] Keep `AGENTS.md` and `TASKS.md` accurate as the repo evolves.
- [ ] Monitor ghost recordings after the recorder-readiness patch; collect evidence if they recur.
- [ ] Rotate or cap `logs/voice-bridge.log` growth (currently gitignored; check disk usage periodically).
- [ ] Add tests for `ptt_pynput.py` debounce/lock and `slide_keyboard.py` drag math.

## Productization (Tahuti in AI-Controller) — synced from ~/PERPETUAL_HANDOFF.md 2026-11-11

- [x] **Crash-loop guard** for `a-button-double-tap-enter.service` — fixed 2026-11-11
  (commit `8def77d`): script polls for the controller instead of exiting 1; unit gained
  `StartLimitIntervalSec=60` / `StartLimitBurst=5`. Evidence: 26,454 restarts Nov 4–Nov 9.
- [x] **In-repo TASKS.md sync** — this section.
- [x] **Benchmark soak** — ✅ 2026-11-11: n=20, fixed 11-04 wording. 20/20 archived done,
  20/20 correct, 0 `failed/`, `NRestarts` 0→0. Interactive single-goal latency 2.79/3.31s;
  batch p95 inflated by 3-worker backlog + one 22s Meta free-tier round (never failed).
  Full record: `~/MD/BENCHMARK-2026-11-04.md` soak section.
- [ ] **Fresh-OS install QA** — run `install.sh` on a clean VM (NOT this machine); walk the
  zero-knowledge path: install → launcher → hold trigger → "slash help" → first approval card.
  File findings as issues, don't hotfix blind.
- [ ] **First-run onboarding** — one-time guided card sequence (RT-dictation, Guide legend,
  View keyboard, "slash help"); building blocks exist in `tahuti_choice_presenter.py`/`tahuti_nudge.py`.
- [ ] **README screenshots** — avatar + approval card + slash-help card as real screenshots.
- [ ] **Quoted-speech fence** — transcripts quoting an action ("it keeps opening YouTube")
  must never become tool calls (design task; see handoff 2026-11-02).
- [ ] **Skills registry** — `~/.sensei/skills` is empty; scaffolding real skills lights up
  the Tahuti↔Sensei handshake.

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
