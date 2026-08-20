# AGENTS.md

Guidance for AI coding agents working in this repo. Read this before touching anything under `scripts/` or `systemd/`.

## What this project is

AI Controller is a Linux desktop accessibility system. It turns a wired Xbox Series X/S controller into a full keyboard/mouse/voice input device so someone with limited mobility (or just sitting on a couch) can run their whole desktop without touching a physical keyboard or mouse. It is a real, daily-used product, not a prototype — see "Project status" below.

## Components

- **AntiMicroX** — third-party app that maps controller buttons/sticks to keyboard keys and mouse events. Profiles live outside this repo in AntiMicroX's own config; `controller-profile-switcher.sh` swaps which profile is loaded.
- **`scripts/ptt_pynput.py`** — push-to-talk listener. Right Trigger is mapped by AntiMicroX to the F13 key; this script watches for F13 down/up, records audio via `parec`, and posts it to the voice bridge for transcription.
- **`scripts/voice_bridge.py`** — FastAPI app on `:8002`. Does speech-to-text via the Groq Whisper API and text-to-speech via `edge-tts`. `ptt_pynput.py` and other callers hit it over HTTP.
- **`scripts/slide_keyboard.py`** — GTK3 floating on-screen keyboard. Sends keystrokes to the focused window via `xdotool` without stealing focus.
- **`scripts/controller-legend.py`** — GTK3 HUD overlay showing current button mappings, toggled with Guide.
- **`scripts/controller-profile-switcher.sh`** — watches the focused window and swaps the active AntiMicroX profile (desktop / browser / YouTube TV) accordingly. Also handles antimicrox process dedup and the F13 xmodmap overlay reapply.
- **`systemd/`** — user services for all of the above (`ptt-pynput.service`, `voice-bridge.service`, `ai-slide-keyboard.service`, `controller-legend.service`, `antimicrox-autoload.service`, `f13-xmodmap-heal.service`, `xone-driver-guard.service`). Services do not autostart on boot unless the user opts in via the launcher.

## Hard rules for agents

- **The active AntiMicroX profile is `desktop`** (see `~/.config/ai-controller/controller_current_profile`). Do not switch profiles, edit profile files, or stop/restart/disable `antimicrox-autoload.service` unless the user explicitly asks for it in the current task. This service is what keeps the controller mapped to the desktop at all — breaking it silently breaks the user's only input device.
- **Real API keys and audio device names live in `~/.config/ai-controller/config.env`** (Groq API key, `AUDIO_INPUT`, `AUDIO_OUTPUT`). This file is outside the repo and is never committed. Never hardcode a key, never print the file's contents into a commit, log that gets committed, or PR description.
- **No hardcoded `/home/elijah` paths in committed code.** The repo already went through a portability pass for this — use `%h`/`$HOME`/relative paths in anything that ships. If you find a regression, fix it.

## Making a safe change

1. Read `systemd/` first if the change touches a running service, so you know its `ExecStart`, `ExecStartPost`, and restart behavior before editing the script it launches.
2. Restart only the service you actually changed (`systemctl --user restart <name>.service`). Don't bounce unrelated services.
3. `ptt_pynput.py` writes raw captures to `/tmp/ptt-debug/` — leave these in place; they're the primary evidence trail when dictation misbehaves. Don't delete the directory as a "cleanup" step.
4. If you touch dictation, test it end-to-end (press-and-hold F13, speak, release, confirm text lands in the focused window) rather than trusting a unit test alone — most of the hard bugs here are timing/hardware races that only show up live.
5. Run `python3 -m py_compile <file>.py` on any Python file you edit before considering the change done.
6. Keep `git status` clean — no stray `.bak`, log, or debug files staged.

## Common failure modes (context, not a to-do list)

- **Ghost recordings** — `parec` can take >1s to actually attach to the PulseAudio source. If the user releases F13 before it's ready, a late-starting recorder would run free until the next press. Guarded by the recorder-readiness gate in `ptt_pynput.py` (`_recorder_ready` event / `_wait_recorder_ready`).
- **"Too short — skipped"** — `voice_bridge.py` rejects captures under a byte threshold. Can be legitimately short audio, or a symptom of the xone-gip card reset race described below stealing the front of a take.
- **TTS barge-in** — pressing F13 while TTS is speaking should immediately cut the audio so the user isn't talked over. Handled by `_mute_tts()` in `ptt_pynput.py`, coordinated with `voice_bridge.py` via a barge file.
- **xone-gip audio buffer wedge** — the xone-gip-headset kernel driver's audio output buffers can wedge over time, producing static or corrupted STT input (`dmesg: gip_send_audio_samples: get buffer failed: -28`). Recovered by cycling the PulseAudio card profile off → wait ~0.7s → on, deferred so it never runs while `parec` is attached or about to attach (see `_pending_audio_reset` / `_fire_deferred_audio_reset` in `ptt_pynput.py`, and `scripts/gip-deep-reset.sh` / `scripts/reset-controller-audio.sh` for the deeper kernel-module-reload recovery path).

## Project status

Core product is shipped and in daily use: push-to-talk dictation, on-screen keyboard, HUD legend, auto profile switching, TTS responses, xone controller support, ghost-recording prevention, and the public-repo portability pass are all done. See `TASKS.md` for the current maintenance backlog — mostly monitoring, test coverage for `ptt_pynput.py`/`slide_keyboard.py`, and log rotation, not open feature work.
