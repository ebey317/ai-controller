# AI Controller Profile - $10 Couch Computing Solution

**Plug in an Xbox/PlayStation controller → talk to AI with voice commands → hear responses. No keyboard. No mouse.**

## What This Is

A standalone, power-loss safe AI controller that turns your gamepad into a voice-controlled computer interface. Works out of the box on Linux (Ubuntu/Debian/Mint), with partial macOS support and manual Windows setup.

### Features
- **Universal Controller Support**: Works with Xbox, PlayStation, or any USB gamepad
- **Voice Control**: Press Right Trigger (RT) to talk, release to listen
- **On-Screen Keyboard**: Use left stick to type without physical keyboard
- **HUD Display**: See button mappings on screen (controller-legend)
- **Auto-Profile Switching**: Browser focus → browser layout, Kodi → TV remote layout
- **Persistent State**: All settings saved in git, survives power loss

## Quick Start

```bash
git clone https://github.com/yourusername/ai-controller-profile
cd ai-controller-profile
bash install.sh
```

## Installation Steps

1. **Run installer**: `bash install.sh`
2. **Connect controller**: Plug in Xbox/PlayStation controller
3. **AntimicroX auto-starts**: First run downloads and sets up AntiMicroX
4. **Profiles auto-activate**: Browser → browser layout, Kodi → TV remote
5. **Push-to-talk**: Right Trigger (RT) = F13 key (bind to your voice software)

## Pricing & Release

- **Price**: $10 (one-time payment)
- **What you get**: Complete installer package with all scripts, profiles, and documentation
- **License**: MIT (free to use, modify, and distribute)

## Support

- **Documentation**: `AI_CONTROLLER_COMPLETE_REFERENCE.md`
- **Troubleshooting**: Check systemd services with `systemctl --user status antimicrox-autoload.service voice-bridge.service ptt-pynput.service`
- **Contact**: Elijah (github.com/ebey317)

## System Requirements

- **OS**: Linux (Ubuntu/Debian/Mint) - fully supported
- **Hardware**: 
  - Controller (Xbox/PS/USB gamepad)
  - Microphone (built-in or external)
  - Speaker/headphones
- **Dependencies**: 
  - Python 3.7+
  - AntiMicroX (auto-installed)
  - Git (for version control)

## How It Works

1. **Controller**: AntiMicroX maps controller buttons to keyboard events
2. **Voice**: ptt_pynput.py listens for F13 (RT button) and records audio
3. **STT**: voice-bridge.py sends audio to Groq Whisper for transcription
4. **Agent**: Transcript sent to AI agent (Hermes or any LLM API)
5. **TTS**: Agent response played back through speakers/headphones

## Power Loss Safety

All configuration files are saved on disk. After power loss:
- Machine boots normally
- AntiMicroX auto-starts via systemd
- Profiles auto-load based on last active window
- No data loss - everything is git-tracked

## Getting Started

1. Clone this repo
2. Run `bash install.sh`
3. Plug in your controller
4. Press Right Trigger (RT) to start talking
5. Say "What's the weather?" and hear the response

## License

MIT License - use it for personal or commercial purposes.