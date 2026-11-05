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

The installer will:
1. Install system dependencies (Python, GTK3, AntiMicroX, mpv)
2. Create a virtual environment with all Python packages from `requirements.txt`
3. Prompt for your Groq API key (stored in `config.env`, not a keychain)
4. **Offer first-run voice & avatar setup** — pick your avatar (Tahuti, Seba, Kunoichi, Ogun) and tune voices
5. Copy AntiMicroX profile with install-path placeholders replaced
6. Ask consent before modifying system driver config (xpad blacklist + initramfs)
7. Install udev rules for antimicrox device access
8. Install systemd user services (core 7 + opt-in Tahuti/headless services; does NOT enable autostart by default; `tahuti-readme-nudge.timer` is the only timer enabled at install)
9. Create a desktop shortcut

## Post-Install

1. Log out and back in for the `input` group membership to take effect
2. Plug in your wired Xbox controller
3. Put on headphones
4. Open the launcher (desktop icon or `python3 scripts/ai-controller-launcher.py`)
5. Click **Fix Controller + Start Services** (recommended first run) or **Start Services**
6. Press **Right Trigger** and talk
7. Press **View** button to toggle the floating keyboard
8. Press **Guide** button for the controller legend HUD

## Voice & Avatar Setup

If you skipped the first-run setup, or want to change voices later:

```bash
# Terminal wizard (full options)
python3 scripts/controller_setup.py

# Graphical panel (from the floating keyboard's SETUP button)
# Opens setup_panel.py with sliders/dropdowns for all settings
```

The wizard lets you:
- **Tune avatar voices** — each avatar (Tahuti, Seba, Kunoichi, Ogun) has its own voice profile (engine, voice ID, rate, pitch, gain, EQ)
- **Tune keyboard voice** — the voice pack used by the on-screen keyboard (Aria/Joe Piper packs, or Edge TTS)
- **Avatar setup** — active avatar, on-screen size, movement pattern, when it appears (talk buttons vs always), approval box, visibility lifecycle
- **Run doctor** — comprehensive health checks
- **Voice board** — graphical rotary-knob panel for fine-tuning

## Uninstall

```bash
bash uninstall.sh
```

Then manually remove system-level changes (xpad blacklist, udev rule) as printed by the uninstaller.

---

**Full documentation:** [README.md](README.md)

---

## Voice-agent privacy: `~/.tahuti/trusted_providers`

Tahuti answers with whatever Sensei model is pinned. Some tools return **your private content** —
`read_file`, `list_dir`, `check_email`, `check_commits` — and that content is only shared with a
model you have explicitly trusted.

- **Trusted by default:** models running **on this computer** (`local`, `ollama`).
- **To trust a cloud provider** (e.g. a Meta or OpenRouter pin), add its name on its own line
  in `~/.tahuti/trusted_providers`:

  ```bash
  echo "meta models" >> ~/.tahuti/trusted_providers   # one name per line, # comments allowed
  ```

- Anything not in that file gets a clear refusal instead of your data — the agent tells you
  exactly which name to add if you want to allow it.
- **Rule of thumb:** private files/mail go only to local models, or providers you have decided
  to trust for this machine.

## README nudge (the avatar's "Want to learn more?" card)

`install.sh` wires a user timer (`tahuti-readme-nudge.timer`) that asks about every ~2 hours
whether you want to open the Tahuti README. Say **Turn off** on the card and it stays quiet;
the card itself is the opt-out. To switch it off from the terminal instead:

```bash
systemctl --user disable --now tahuti-readme-nudge.timer
```

## Troubleshooting

Run the built-in doctor any time something seems off — it checks the install, the venv,
every systemd unit, and the AntiMicroX profile in one pass:

```bash
bash scripts/doctor.sh
```

It exits 0 with `All checks passed` when everything is healthy, and names exactly what is
broken (and where) when it is not. Opt-in headless services are allowed to be disabled on a
desktop install — doctor will not fail you for that.

To uninstall, run `bash uninstall.sh`, then remove system-level changes (xpad blacklist,
udev rule) as printed by the uninstaller. Per-service stop/autostart toggles live in the
AI Controller launcher; installed units live in `~/.config/systemd/user/`.
