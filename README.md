# AI Controller

> Voice-first desktop accessibility and HCI automation for Linux. Control your entire desktop with an Xbox controller — push-to-talk dictation, floating keyboard, mouse movement, and HUD overlay.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Last Commit](https://img.shields.io/github/last-commit/ebey317/ai-controller)](https://github.com/ebey317/ai-controller)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)]()
[![Platform: Linux](https://img.shields.io/badge/Platform-Linux-blue.svg)]()
[![Version](https://img.shields.io/badge/Version-1.0.0-brightgreen.svg)](https://github.com/ebey317/ai-controller/releases/tag/v1.0.0)

AI Controller turns a wired Xbox Series X/S controller into a complete desktop input device. It was built for couch use, bed use, limited mobility, and any situation where a keyboard and mouse are not within reach.

---

## What It Does

- **Push-to-talk speech-to-text** — press Right Trigger, speak, release; your words are transcribed by Groq Whisper and typed into the focused window.
- **Floating on-screen keyboard** — toggle with the View button; a GTK keyboard that sends keystrokes without stealing focus.
- **Controller legend HUD** — toggle with the Guide button to see your current button mappings as a floating overlay.
- **Auto profile switching** — swaps AntiMicroX layouts between desktop, browser, and YouTube TV based on the focused window.
- **Voice response (TTS)** — answers read back aloud via edge-tts through the FastAPI voice bridge.
- **Mouse and scroll control** — left stick moves the cursor, right stick scrolls.
- **Systemd-managed services** — start/stop from the launcher app, with optional opt-in autostart.

---

## Why It Exists

Desktop computing assumes a keyboard and mouse are always within reach. That assumption breaks on a couch, in bed, or for users with limited mobility. AI Controller removes that assumption by turning a $30 game controller into a full input device — voice, keyboard, mouse, and profile switching in one.

---

## Tech Stack

| Component | Technology | Purpose |
|---|---|---|
| Button mapping | [AntiMicroX](https://github.com/AntiMicroX/antimicrox) | Controller → keyboard/mouse events |
| Push-to-talk | Python + [pynput](https://github.com/moses-palmer/pynput) | F13 hotkey listener, audio capture |
| Voice bridge | [FastAPI](https://fastapi.tiangolo.com/) + [uvicorn](https://www.uvicorn.org/) | STT + TTS on `:8002` |
| On-screen keyboard | Python + GTK3 (PyGObject) | Floating keyboard, xdotool keystroke injection |
| HUD legend | Python + GTK3 | Floating controller button-map overlay |
| Profile switching | Bash + xdotool | Window-focus watcher, AntiMicroX profile swap |
| TTS | [edge-tts](https://github.com/rhasspy/rhasspy-edge-tts) | Cloud text-to-speech |
| Service management | systemd user units | Start/stop via launcher, crash recovery, optional autostart |
| Device rules | udev | USB autosuspend control, controller ACLs, xone/xpad driver guard |

---

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

# Python dependencies (venv created automatically during install)
# See requirements.txt: httpx fastapi uvicorn pynput numpy scipy edge-tts

# TTS playback
sudo apt install mpv

# GTK3 (on-screen keyboard + HUD)
sudo apt install python3-gi gir1.2-gtk-3.0

# Groq API key — free tier available at https://console.groq.com/keys
# The installer prompts you to paste it.
```

**Supported platforms:** Ubuntu / Mint / Debian (apt) with wired Xbox Series X/S controller (045e:0b12). Linux only.

---

## Usage

```bash
# Open the launcher (or click the AI Controller desktop icon)
python3 scripts/ai-controller-launcher.py
```

In the launcher:
- **Start AI Controller** — start all services
- **Stop AI Controller** — stop all services
- **Start on boot** — opt-in autostart (off by default)

**On the controller:**

1. Plug in your wired Xbox Series X/S controller.
2. Put on headphones.
3. Press **Right Trigger** and talk — your speech is transcribed and typed.
4. Press **View** to toggle the floating keyboard.
5. Press **Guide** to toggle the controller legend HUD.

**Profile switching:**

```bash
# Auto-switch profiles based on focused window
nohup DISPLAY=:0 bash scripts/controller-profile-switcher.sh &

# Lock to desktop profile
touch ~/.config/ai-controller/lock_desktop_profile
```

---

## Architecture

```text
┌──────────────────────────────────────────────────────────────────┐
│                        Xbox / PS Controller                      │
│  ┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐    │
│  │   RT   │  │  View  │  │ Guide  │  │   LS   │  │   RS   │    │
│  │ (Talk) │  │ (Kbd)  │  │ (HUD)  │  │(Mouse) │  │(Scroll)│    │
│  └────────┘  └────────┘  └────────┘  └────────┘  └────────┘    │
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
│  antimicrox-autoload │ f13-xmodmap-heal │ xone-driver-guard       │
└──────────────────────────────────────────────────────────────────┘
```

---

## Voice Bridge API

The voice bridge runs on `http://localhost:8002`.

| Endpoint | Description |
|---|---|
| `GET /health` | Service health check |
| `POST /voice?mode=transcribe_only` | Transcribe audio file |
| `POST /voice?mode=tts_only` | Generate TTS from text |
| `POST /voice?mode=both` | Transcribe audio, then read response aloud |

```bash
# Health check
curl http://localhost:8002/health

# Transcribe audio
curl -X POST http://localhost:8002/voice?mode=transcribe_only \
  -F "audio=@recording.wav"

# Generate TTS
curl -X POST http://localhost:8002/voice?mode=tts_only \
  -d "text=Hello, world!"
```

---

## Systemd Services

| Service | Purpose |
|---|---|
| `ptt-pynput.service` | Listens for F13 key presses and captures audio |
| `voice-bridge.service` | FastAPI STT/TTS service |
| `ai-slide-keyboard.service` | On-screen keyboard overlay |
| `controller-legend.service` | Controller button mapping overlay |
| `antimicrox-autoload.service` | Auto-loads the default controller profile |
| `f13-xmodmap-heal.service` | Restores F13 key mapping after X reloads or controller hotplugs |
| `xone-driver-guard.service` | Blocks in-kernel `xpad` driver and prevents USB autosuspend on the controller |

```bash
systemctl --user start/stop/restart <service-name>.service
systemctl --user enable/disable <service-name>.service
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Right trigger doesn't trigger dictation | `bash scripts/fix-f13-keymap.sh` — reapplies F13 xmodmap overlay |
| PTT service can't see AntiMicroX devices | `sudo udevadm trigger --action=add --subsystem-match=input && systemctl --user restart ptt-pynput.service` |
| Duplicate AntiMicroX processes | `systemctl --user restart antimicrox-autoload.service` |

---

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Format Python code: `black scripts/ tests/`
4. Run linters: `ruff check . && black --check . && shellcheck *.sh`
5. Commit changes: `git commit -m "Add my feature"`
6. Push to your fork: `git push origin feature/my-feature`
7. Open a Pull Request

---

## License

[MIT License](LICENSE) — © 2026 Elijah Wilkins.

Use it, modify it, resell your own builds.

---

## Author / Contact

**Elijah Wilkins**

- GitHub: [@ebey317](https://github.com/ebey317)
- Repository: [ebey317/ai-controller](https://github.com/ebey317/ai-controller)

### Related Repositories

| Repo | Purpose |
|---|---|
| [`ebey317/ai-controller-profile`](https://github.com/ebey317/ai-controller-profile) | Profiles, scripts, systemd units, and reference docs |
| [`ebey317/master-ai-cli`](https://github.com/ebey317/master-ai-cli) | Local-first AI agent runtime that can drive this accessibility stack |
