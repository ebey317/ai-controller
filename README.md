# AI Controller

Xbox controller to keyboard/mouse/voice mapping for desktop accessibility.

![AI Controller Logo](logo.png)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Last Commit](https://img.shields.io/github/last-commit/ebey317/ai-controller)](https://github.com/ebey317/ai-controller)
[![Language: Python](https://img.shields.io/badge/Language-Python-blue.svg)]()
[![Platform: Linux](https://img.shields.io/badge/Platform-Linux-blue.svg)]()
[![Version](https://img.shields.io/badge/Version-1.0.0-brightgreen.svg)](https://github.com/ebey317/ai-controller/releases/tag/v1.0.0)

> Talk to your computer from the couch. No keyboard. No mouse. Just a controller and headphones.

## What It Does

AI Controller maps an Xbox controller's buttons to keyboard, mouse, and voice actions so you can run your entire desktop without leaving the chair. Press a trigger to talk, a button to toggle a floating keyboard, and a stick to move the mouse — all through a single controller.

The system uses **AntiMicroX** for button-to-key mapping, a **push-to-talk** Python listener for speech-to-text, a **FastAPI voice bridge** for transcription and TTS, a **GTK on-screen keyboard** for typing without a physical keyboard, and a **controller legend HUD** that shows your current button mappings as a floating overlay.

## Why It Exists

Desktop computing assumes you have a keyboard and mouse within reach. When you're on a couch, in bed, or have limited mobility, that assumption breaks. AI Controller turns a $30 game controller into a complete desktop input device — voice dictation, on-screen typing, mouse movement, and context-aware profile switching — so the computer is usable from anywhere in the room.

## Features

- 🎤 **Push-to-talk speech-to-text** — press Right Trigger, speak, release; your words are typed into the active window via Groq Whisper transcription
- ⌨️ **Floating on-screen keyboard** — toggle with the View button, type with the stick; a real GTK keyboard that sends keystrokes to the focused window without stealing focus
- 🕹️ **Controller legend HUD** — floating overlay showing your current button mappings so you never have to look down
- 🔄 **Auto profile switching** — `controller-profile-switcher.sh` watches the focused window and swaps AntiMicroX layouts (desktop, browser, YouTube TV) automatically
- 🔊 **Voice response (TTS)** — `voice_bridge.py` reads answers aloud through your headphones via edge-tts
- 🎮 **Xbox controller support (wired)** — built around the Xbox Series X/S wired controller (045e:0b12)
- 🔌 **Power-loss safe** — systemd user services with crash recovery; xone-only driver guard enabled on Linux
- ☑️ **Opt-in autostart** — services do NOT run on boot by default. Use the launcher's "Start on boot" checkbox to enable
- 📋 **Dictation modes** — PRO, BUBBLY, CASUAL, BOLD, BIG text personality transforms
- 🔧 **Udev rules + driver guard** — USB autosuspend fixes and hard-block of the in-kernel `xpad` driver that breaks xone audio

## Tech Stack

| Component | Technology | Purpose |
|---|---|---|
| Button mapping | [AntiMicroX](https://github.com/AntiMicroX/antimicrox) | Controller → keyboard/mouse event translation |
| Push-to-talk | Python + [pynput](https://github.com/moses-palmer/pynput) | F13 hotkey listener, audio capture |
| Voice bridge | [FastAPI](https://fastapi.tiangolo.com/) + [uvicorn](https://www.uvicorn.org/) | Speech-to-text (Groq Whisper) and TTS endpoint on :8002 |
| On-screen keyboard | Python + GTK3 (PyGObject) | Floating keyboard, xdotool keystroke injection |
| HUD legend | Python + GTK3 | Floating controller button-map overlay |
| Profile switching | Bash + xdotool | Window-focus watcher, AntiMicroX profile swap |
| TTS voices | [edge-tts](https://github.com/rhasspy/rhasspy-edge-tts) | Cloud text-to-speech (requires internet) |
| Service management | systemd user units | Start/stop via launcher app, crash recovery, optional autostart |
| Device rules | udev | USB autosuspend control, antimicrox device ACLs |

## Quick Start

```bash
git clone https://github.com/ebey317/ai-controller.git
cd ai-controller
bash install.sh
```

**Prerequisites:**

```bash
# AntiMicroX
sudo apt install antimicrox

# Python dependencies (creates venv automatically during install)
# See requirements.txt for the full list:
#   httpx fastapi uvicorn pynput numpy scipy edge-tts

# Edge TTS (cloud-based, requires internet)
sudo apt install mpv
pip install edge-tts

# GTK3 (on-screen keyboard + HUD)
sudo apt install python3-gi gir1.2-gtk-3.0

# Groq API key (free tier available — required for voice dictation)
# Get one at https://console.groq.com/keys
# The installer will prompt you to paste it.
```

**Supported platforms:** Ubuntu / Mint / Debian (apt) with wired Xbox Series X/S controller. Linux only — macOS and Windows are not supported.

**Distribution note:** This product is designed for Linux desktop (X11). It requires a wired Xbox Series X/S controller, a Groq API key (free tier available), and an internet connection for voice dictation. All software dependencies are free and open-source.

## Usage

The AI Controller desktop app is the primary way to start and stop services.

```bash
# Open the launcher (or click the AI Controller desktop icon)
python3 scripts/ai-controller-launcher.py
```

In the launcher:
- Click **Start AI Controller** to start all services
- Click **Stop AI Controller** to stop everything
- Check **Start on boot** to autostart on login (off by default)

**On the controller:**

1. Plug in your wired Xbox Series X/S controller
2. Put on headphones
3. Press **Right Trigger** and talk — your speech is transcribed and typed
4. Press **View** to toggle the floating keyboard
5. Press **Guide** to toggle the controller legend HUD

**Profile switching:**

```bash
# Auto-switch profiles based on focused window
nohup DISPLAY=:0 bash scripts/controller-profile-switcher.sh &

# Lock to desktop profile
touch ~/.config/ai-controller/lock_desktop_profile
```

**Voice bridge API:**

```bash
# Health check
curl http://localhost:8002/health

# Transcribe audio
curl -X POST http://localhost:8002/voice?mode=transcribe_only \
  -F "audio=@recording.wav"
```

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        Xbox / PS Controller                      │
│  ┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐      │
│  │   RT   │  │  View  │  │ Guide  │  │   LS   │  │   RS   │      │
│  │ (Talk) │  │(Kbd)   │  │ (HUD)  │  │(Mouse) │  │(Scroll)│      │
│  └────────┘  └────────┘  └────────┘  └────────┘  └────────┘      │
└──────────────────────────────────────────────────────────────────┘
       │           │            │           │           │
       ▼           ▼            ▼           ▼           ▼
┌──────────┐ ┌───────────┐ ┌──────────┐ ┌──────────────────────┐
│ AntiMicroX│ │slide_     │ │controller│ │  Mouse movement     │
│ → F13     │ │keyboard.py│ │legend.py │ │  (xtest event gen)  │
└──────────┘ └───────────┘ └──────────┘ └──────────────────────┘
     │ F13
     ▼
┌──────────────┐     ┌──────────────────┐     ┌──────────────┐
│ ptt_pynput.py│────▶│  voice_bridge.py  │────▶│  xdotool     │
│ (hotkey      │     │  (FastAPI :8002)  │     │  (type text  │
│  listener)   │     │  Groq Whisper STT │     │   into app)  │
└──────────────┘     └──────────────────┘     └──────────────┘
                            │
                     mode?  ▼
                 ┌──────────────────┐
                 │  TTS (edge-tts)   │
                 │  → mpv            │
                 └──────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│              controller-profile-switcher.sh                      │
│  watches focused window → swaps AntiMicroX profile               │
│  desktop │ browser │ YouTube TV                                   │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│                    systemd user services                          │
│  ptt-pynput │ voice-bridge │ ai-slide-keyboard │ controller-legend │
│  antimicrox-autoload │ f13-xmodmap-heal                          │
└──────────────────────────────────────────────────────────────────┘
```

## Troubleshooting

Common issues:

- **Right trigger doesn't trigger dictation** — Run `bash scripts/fix-f13-keymap.sh` to re-apply the xmodmap F13 overlay (gets wiped on X server reloads or controller hotplugs)
- **PTT service can't see antimicrox devices** — Run `sudo udevadm trigger --action=add --subsystem-match=input` then `systemctl --user restart ptt-pynput.service` (ACLs need re-applying after antimicrox restart)
- **Duplicate antimicrox processes** — Run `systemctl --user restart antimicrox-autoload.service` (dedup logic is built into controller-profile-switcher.sh)

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Format Python code: `black scripts/ tests/`
4. Commit changes: `git commit -m "Add my feature"`
5. Push to your fork: `git push origin feature/my-feature`
6. Open a Pull Request

Please run `black` on all Python files before submitting. Keep shell scripts `shellcheck`-clean.

## API Documentation

The voice bridge service runs on `http://localhost:8002` and provides the following endpoints:

- `GET /health` - Returns service health status
- `POST /voice` - Transcribes audio or generates TTS

### Transcribe audio

```bash
curl -X POST http://localhost:8002/voice?mode=transcribe_only \
  -F "audio=@recording.wav"
```

### Generate TTS

```bash
curl -X POST http://localhost:8002/voice?mode=tts_only \
  -d "text=Hello, world!"
```

### Combined mode

```bash
curl -X POST http://localhost:8002/voice?mode=both \
  -F "audio=@recording.wav"
```

## Service Documentation

The system consists of the following services:

- `ptt-pynput.service` - Listens for F13 key presses and captures audio
- `voice-bridge.service` - FastAPI service for speech-to-text and text-to-speech
- `ai-slide-keyboard.service` - On-screen keyboard overlay
- `controller-legend.service` - Controller button mapping overlay
- `antimicrox-autoload.service` - Auto-loads the default controller profile
- `f13-xmodmap-heal.service` - Restores F13 key mapping when needed
- `xone-driver-guard.service` - Prevents USB autosuspend on the Xbox controller

All services are managed via systemd user units and can be controlled with:

```bash
systemctl --user start/stop/restart <service-name>.service
systemctl --user enable/disable <service-name>.service
```

## Services & API

AI Controller exposes a small HTTP surface for voice and status:

| Service | Endpoint / Script | Purpose |
|---|---|---|
| Voice bridge | `scripts/voice_bridge.py` (FastAPI on `:8002`) | STT via Groq Whisper + TTS via edge-tts |
| Push-to-talk | `scripts/ptt_pynput.py` | Watches F13 (Right Trigger) and sends audio to voice bridge |
| On-screen keyboard | `scripts/slide_keyboard.py` | GTK keyboard that types into the focused window |
| Controller legend | `scripts/controller-legend.py` | Floating HUD showing the active button map |
| Profile switcher | `scripts/controller-profile-switcher.sh` | Watches the active window and swaps AntiMicroX profiles |

All systemd services live under `systemd/` and are controlled with `systemctl --user`.

---

## Development Setup

1. Clone the repository: `git clone https://github.com/ebey317/ai-controller.git`
2. Install dependencies: `bash install.sh`
3. Set up pre-commit hooks: `pre-commit install`
4. Run linters: `ruff check . && black --check . && isort --check-only .`
5. Run tests: `pytest`

## Testing

Tests are located in the `tests/` directory and can be run with:

```bash
pytest --tb=short -v --cov=scripts --cov=tests --cov-report=term-missing
```

The test suite includes:

- `test_utils.py` - Tests for utility functions and repo structure
- `test_ai_controller_paths.py` - Tests for path/config helpers
- `test_voice_bridge.py` - Tests for the voice bridge API

## Pull Request Guidelines

- All Python code must pass `ruff` and `black` checks
- All shell scripts must pass `shellcheck`
- All new features must have accompanying tests
- Documentation must be updated for any new features or API changes
- CI must pass before merging

## License

[MIT License](LICENSE) — © 2026 Elijah Wilkins

Use it, modify it, resell your own builds.

## Author / Contact

**Elijah Wilkins**

- GitHub: [@ebey317](https://github.com/ebey317)
- Repository: [ebey317/ai-controller](https://github.com/ebey317/ai-controller)