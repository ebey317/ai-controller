# AI Controller — Installation Guide

For complete installation instructions, see the **[Quick Start](README.md#quick-start)** section in the main README.

## Prerequisites

1. **Linux desktop (X11)** — Ubuntu 22.04+, Mint 21+, or Debian 12+
2. **Wired Xbox Series X/S controller** (045e:0b12)
3. **Groq API key** — free at https://console.groq.com/keys (required for voice dictation)
4. **Internet connection** — needed for Groq Whisper STT and edge-tts
5. **AntiMicroX** — `sudo apt install antimicrox`
6. **GTK3** — `sudo apt install python3-gi gir1.2-gtk-3.0`
7. **mpv** — `sudo apt install mpv` (for TTS audio playback)

## Quick Install

```bash
git clone https://github.com/ebey317/ai-controller.git
cd ai-controller
bash install.sh
```

## What the Installer Does

1. **Installs system dependencies** (Python, GTK3, AntiMicroX, mpv)
2. **Creates a virtual environment** with all Python packages from `requirements.txt`
3. **Prompts for your Groq API key** (stored in `config.env`, not a keychain)
4. **Copies AntiMicroX profile** with install-path placeholders replaced
5. **Asks consent** before modifying system driver config (xpad blacklist + initramfs)
6. **Installs udev rules** for antimicrox device access
7. **Installs 6 systemd user services** (does NOT enable autostart by default)
8. **Creates a desktop shortcut**

## Post-Install

1. Log out and back in for the `input` group membership to take effect
2. Plug in your wired Xbox controller
3. Put on headphones
4. Open the launcher (desktop icon or `python3 scripts/ai-controller-launcher.py`)
5. Click **Start AI Controller**
6. Press **Right Trigger** and talk
7. Press **View** button to toggle the floating keyboard

## Uninstall

```bash
bash uninstall.sh
```

Then manually remove system-level changes (xpad blacklist, udev rule) as printed by the uninstaller.

---

**Full documentation:** [README.md](README.md)