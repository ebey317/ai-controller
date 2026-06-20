# AI Controller Profile

> **Plug in an Xbox or PlayStation controller → talk to AI with your voice → hear the response. No keyboard. No mouse.**

A standalone, power-loss-safe AI controller that turns any USB gamepad into a voice-controlled desktop interface. Runs on Linux (Ubuntu/Debian/Mint).

---

## What it does

```
Controller button (RT)
        │  F13 keypress via AntiMicroX
        ▼
ptt_pynput.py     ← listens for F13, records mic audio
        │  WAV audio POST
        ▼
voice_bridge.py   ← Groq Whisper STT → transcript
        │  transcript
        ▼
CLAF / LLM        ← generates response
        │  response text
        ▼
edge-tts + mpv    ← speaks response through headphones
```

Profile auto-switching: AntiMicroX detects the focused window and loads the matching layout automatically.

| Active window | Profile loaded |
|---|---|
| Browser | `ai-browser.amgp` — click, scroll, tab nav |
| Desktop / terminal | `ai-desktop.amgp` — mouse, keyboard shortcuts |
| Kodi / IPTV | `ai-iptv.amgp` — media controls |

---

## Button layout (default desktop profile)

| Button | Action |
|---|---|
| **RT** (hold) | Push-to-talk — record voice |
| **LB** | Scroll up |
| **RB** | Scroll down |
| **Start** | Open terminal |
| **Left stick** | Mouse movement |
| **A / B / X / Y** | Left click / Right click / Back / Enter |
| **D-pad** | Arrow keys |

Full reference: `AI_CONTROLLER_COMPLETE_REFERENCE.md`

---

## Repository layout

```
ai-controller-profile/
├── profiles/
│   ├── ai-desktop.amgp         # Desktop / terminal layout
│   ├── ai-browser.amgp         # Browser layout
│   ├── ai-iptv.amgp            # IPTV / Kodi layout
│   └── *.gamepad               # AntiMicroX native format copies
├── scripts/
│   ├── voice_bridge.py         # FastAPI STT + LLM + TTS server (port 8002)
│   ├── ptt_pynput.py           # Push-to-talk keyboard listener (F13)
│   ├── controller-legend.py    # On-screen HUD showing button map
│   ├── controller-profile-switcher.sh  # Window-focus → profile swap
│   ├── slide_keyboard.py       # On-screen keyboard (left stick input)
│   └── hermes_tts_play.sh      # mpv playback helper with lowpass filter
├── systemd/
│   ├── antimicrox-autoload.service   # Auto-start AntiMicroX on login
│   ├── ptt-pynput.service            # Push-to-talk daemon
│   └── voice-bridge.service          # Voice bridge server daemon
├── snapshots/                  # Profile backups before edits
├── tests/
│   └── test_voice_bridge.py
├── .env.example                # All configurable env vars
├── requirements.txt            # Python dependencies
└── install.sh                  # Full installer
```

---

## Setup

### 1. Prerequisites

- Linux (Ubuntu 22.04+ / Debian 12+ / Mint 21+)
- Python 3.9+
- USB gamepad (Xbox Series X/S, Xbox One, or PlayStation)
- Microphone (built-in or external)
- [CLAF](https://github.com/ebey317/claf) or any OpenAI-compatible LLM endpoint running locally

### 2. Install

```bash
git clone https://github.com/ebey317/ai-controller-profile.git
cd ai-controller-profile
bash install.sh
```

The installer:
- Downloads AntiMicroX 3.5.1 (requires `configversion=19` schema)
- Copies profiles to AntiMicroX's config directory
- Installs systemd user services
- Enables auto-start on login

### 3. Python dependencies

```bash
pip install -r requirements.txt
```

### 4. System packages

```bash
sudo apt install mpv python3-gi gir1.2-gtk-3.0 xdotool
pip install edge-tts        # TTS engine (not available via apt)
```

### 5. Configure environment

```bash
cp .env.example ~/.config/ai-controller/config.env
# Edit the file — set GROQ_API_KEY to your Groq key
```

Get a free Groq API key at [console.groq.com](https://console.groq.com) — Whisper transcription and LLM responses both use the free tier.

### 6. Start services

```bash
systemctl --user enable --now antimicrox-autoload.service
systemctl --user enable --now ptt-pynput.service
systemctl --user enable --now voice-bridge.service
```

---

## Usage

1. Plug in your controller
2. Hold **Right Trigger (RT)** and speak
3. Release RT — audio is transcribed and sent to the LLM
4. Response plays back through your speakers/headphones

The on-screen HUD (`controller-legend.py`) shows the active button map. It updates automatically when the profile switches.

---

## Power-loss safety

All config is on disk and in git. After a crash or power loss:

- Machine boots → systemd starts all three services automatically
- AntiMicroX loads the last active profile
- Nothing is lost — state lives in files, not memory

---

## Troubleshooting

```bash
# Check service status
systemctl --user status antimicrox-autoload.service
systemctl --user status ptt-pynput.service
systemctl --user status voice-bridge.service

# View live logs
journalctl --user -u voice-bridge.service -f

# Test the voice bridge directly
curl -s http://127.0.0.1:8002/voice \
  -F "text=hello world" \
  -F "mode=transcribe_only"
```

Common issues:

| Symptom | Fix |
|---|---|
| No TTS audio | Check `mpv` is installed and the correct audio sink is selected |
| Push-to-talk not recording | Verify microphone input device with `arecord -l` |
| Profile not switching | Check `controller-profile-switcher.sh` is running; verify AntiMicroX window rules |
| `GROQ_API_KEY` missing | Edit `~/.config/ai-controller/config.env` |

---

## Related

- **[CLAF](https://github.com/ebey317/claf)** — Local LLM proxy; voice bridge routes through this by default
- **[master-ai](https://github.com/ebey317/master-ai)** — Full terminal agent stack; controller is a front-end to it
- **[AI Controller (canonical)](https://github.com/ebey317/-AI-controller.)** — Standalone product build with installer

---

## License

MIT — use freely for personal or commercial purposes.
