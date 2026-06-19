# AI Controller — Complete Reference

**The single source of truth for the entire Xbox controller → Linux desktop system.**
**Last verified: 2026-06-18 on Elijah (elijah-MS-7B86, Ubuntu 22.04/Mint 21.3, X11)**

---

## 1. Overview

One Xbox Series X controller drives the entire Linux desktop: mouse, keyboard,
web browsing, IPTV/TV remote, AND voice dictation — with profiles that
auto-switch based on the focused window. No physical keyboard or mouse required.

**GitHub repo (standalone):** https://github.com/ebey317/ai-controller-profile

**Architecture:**

```
Xbox Controller (USB/BT)
    │
    ▼
AntiMicroX 3.5.1 (antimicrox.AppImage)
    │  Translates gamepad buttons → X11 keycodes / mouse events / execute commands
    │  Profile auto-switches based on focused window class
    │
    ├──→ Desktop profile (ai-desktop.amgp)     [default fallback]
    ├──→ Browser profile (ai-browser.amgp)     [Chrome/Firefox/Edge/Brave]
    └──→ IPTV profile (ai-iptv.amgp)           [MPV/VLC/Kodi/Hypnotix]
    
    Special outputs:
    ├──→ RT → F13 (0x100003c) → ptt_pynput.py → Groq Whisper STT → xdotool type
    ├──→ View button → execute toggle-slide-keyboard.sh → slide_keyboard.py
    └──→ Left stick → mouse cursor, Right stick → scroll
```

---

## 2. Button Mappings — Desktop Profile (ai-desktop.amgp)

**This is the LIVE verified config as of 2026-06-18.**

| Button | AntiMicroX Index | Action | Mode | Code |
|--------|-----------------|--------|------|------|
| **A** | 1 | Left click | mousebutton | 1 |
| **B** | 2 | Backspace | keyboard | 0x1000003 |
| **X** | 3 | Delete | keyboard | 0x1000007 |
| **Y** | 4 | Super/Meta (app launcher) | keyboard | 0x1000022 |
| **View/Back (⧉)** | 5 | Toggle slide_keyboard.py | execute | /home/elijah/scripts/toggle-slide-keyboard.sh |
| **Guide (Xbox button)** | 6 | Super + Tab (Alt-Tab) | keyboard | 0x1000022 + 0x1000001 |
| **Menu/Start** | 7 | Tab | keyboard | 0x1000001 |
| **LS click** | 8 | Space | keyboard | 0x20 |
| **RS click** | 9 | Return | keyboard | 0x1000004 |
| **LB** | 10 | Shift | keyboard | 0x1000020 |
| **RB** | 11 | Middle click | mousebutton | 3 |
| **RT** | — | F13 → Push-to-Talk dictation | keyboard | 0x100003c |
| **Left stick** | — | Mouse cursor | mousemovement | — |
| **Right stick** | — | Scroll wheel | mousemovement | — |

### Browser Profile (ai-browser.amgp)

| Button | Action |
|--------|--------|
| A | Left click + Enter |
| B | Alt+Left (back) |
| X | Middle click |
| Y | Ctrl+V (paste) |
| View/Back | Tab |
| Guide | Middle click |
| Menu/Start | Return |
| LS click | Space |
| RS click | Return |
| LB | Space |
| RB | F13 (PTT) |

### IPTV Profile (ai-iptv.amgp)

| Button | Action |
|--------|--------|
| A | Return (Play/Pause) |
| B | Escape (Stop/Back) |
| X | Ctrl+C |
| Y | Ctrl+V (paste) |
| View/Back | Tab |
| Guide | Ctrl+Tab |
| Menu/Start | Ctrl+L |
| LS click | Space |
| RS click | Set (profile reset) |
| LB | Return |
| RB | Ctrl+W (close tab) |

---

## 3. Voice Pipeline (STT — Speech to Text)

Hold **RT** → speak → release → your words type into the focused window.

```
RT (controller)
  → AntiMicroX maps to F13 (0x100003c)
  → ptt_pynput.py catches F13 via pynput keyboard listener
  → arecord captures audio from Xbox headset mic (PulseAudio default source)
  → POST to voice-bridge on localhost:8002
  → Groq Whisper (whisper-large-v3-turbo) transcribes
  → xdotool types text into focused window
```

### Why pynput, not evdev

AntiMicroX holds an exclusive `EVIOCGRAB` on the controller. A second evdev
reader opens the device but receives zero events. Since AntiMicroX already
translates RT→F13, ptt_pynput.py listens for **F13 at the keyboard layer**
with pynput — no grab conflict.

### Smart routing in ptt_pynput.py

- **Browser windows** (Chrome/Firefox/Brave/Edge): transcript sent to Sensei
  focus engine via `window.__senseiFocus('set-text', ...)` instead of xdotool
- **Discord windows**: transcript copied to clipboard (no auto-type into wrong
  channel), queued to `~/.cache/ptt_discord_queue.txt`
- **Everything else**: xdotool types directly at cursor position

### Audio gotchas

- Xbox jack enumerates only when `xone-gip-headset` module is loaded AND
  something is plugged into the controller jack
- PulseAudio claims the device on connect → set as default source:
  `pactl set-default-source alsa_input.usb-Microsoft_Controller_*`
- Persisted in `~/.config/pulse/default.pa` (keeps analog headphones as
  default sink so TTS/video audio doesn't hijack to controller)

---

## 4. Voice Models (TTS — Text to Speech)

Three voice models are configured across two engines:

### Piper (Offline TTS)

| Model | Gender | Path | Size | Status |
|-------|--------|------|------|--------|
| en_US-joe-medium.onnx | Male | /home/elijah/.local/share/piper/en_US-joe-medium.onnx | ~63MB | Working — Hermes Piper default |
| en_US-lessac-medium.onnx | Female | /home/elijah/scripts/voices/en_US-lessac-medium.onnx | ~63MB | Working — fine-tuned woman voice |
| en_US-amy-medium.onnx | Female | /home/elijah/scripts/voices/en_US-amy-medium.onnx | 0 bytes | BROKEN — download failed |

**Lessac (female) config — the fine-tuned woman profile:**
- Dataset: lessac
- Sample rate: 22050Hz
- Quality: medium
- Piper version: 1.0.0
- JSON config: /home/elijah/scripts/voices/en_US-lessac-medium.onnx.json
- voice_config.json settings:
  - length_scale: 1.1 (slightly slower for clarity)
  - inter_sentence_pause: 0.6
- Cloud backup: /home/elijah/projects/master-ai-context/CONFIGS/STT_WORKING_CONFIG_2026-06-18/voice_config.json

**NOTE:** The lessac .json at ~/.local/share/piper/ is 0 bytes (empty). The
real config lives at ~/scripts/voices/en_US-lessac-medium.onnx.json. To switch
Hermes from joe to lessac, update ~/.hermes/config.yaml tts voice path AND
copy the json config to the same directory as the .onnx file.

### Edge TTS (Online, higher fidelity)

- Voice: en-US-AriaNeural
- Pitch: -22Hz
- Rate: +12%
- Used by voice_bridge.py for spoken responses
- Fallback: spd-say if Edge TTS unavailable

### TTS Playback Pipeline

```
TTS engine (Piper or Edge)
  → generates .wav or .mp3
  → ~/scripts/hermes_tts_play.sh
  → mpv --no-video --audio-device="pulse/alsa_output.pci-0000_28_00.4.analog-stereo" --af=lowpass=f=3000
  → Analog 3.5mm headphone output
```

**hermes_tts_play.sh** routes audio to motherboard analog sink (not controller
headphones) with a lowpass filter at 3000Hz for voice clarity.

**Hermes CLI** does NOT auto-play TTS. Playback is per-utterance via the
playback script. Config: `tts.provider=piper` in ~/.hermes/config.yaml.

---

## 5. On-Screen Keyboard (slide_keyboard.py)

### Problem

Onboard's accessibility "scanner" mode is broken — stuck on backtick key,
confirmed 2026-06-16 independent of the controller. Replaced entirely.

### Solution

**slide_keyboard.py** — Custom GTK floating keyboard:
- Every key is a real GTK button (no scanner, no highlight-and-select)
- Click via controller A + L-stick-as-mouse, or real mouse
- Sends keystrokes via xdotool to whatever window has focus
- `set_accept_focus(False)` — keyboard never steals focus from target window
- SIGUSR1 toggles visibility (show/hide without killing process)
- `--show` flag for immediate appearance on launch
- Dark/orange HUD theme matching the controller-legend overlay

### Keyboard Layout (updated 2026-06-18)

**Lower layer (default):**
- Row 1: ` 1 2 3 4 5 6 7 8 9 0 - = [backspace]
- Row 2: [tab] q w e r t y u i o p [ ] \
- Row 3: [shift] a s d f g h j k l ; ' [enter]
- Row 4: [ctrl] z x c v b n m , . / [shift]

**Upper layer (Shift held):**
- Row 1: ~ ! @ # $ % ^ & * ( ) _ + [backspace]
- Row 2: [tab] Q W E R T Y U I O P { } |
- Row 3: [shift] A S D F G H J K L : " [enter]
- Row 4: [ctrl] Z X C V B N M < > ? [shift]

### Ctrl/Alt Combo Support (added 2026-06-18)

- Hold LT (Ctrl) or Alt on controller → open keyboard (View) → click any letter
- Sends ctrl+letter or alt+letter to the focused app
- Uses one-shot Gdk.Keymap modifier state read (no listener)
- No conflict with ptt_pynput.py — state is read once per keystroke, not held

### Toggle Script

**toggle-slide-keyboard.sh** — Wired to View button (button 5):
- First press: launches slide_keyboard.py with `--show` (visible immediately)
- Second press: sends SIGUSR1 to hide (process stays alive)
- Third press: SIGUSR1 again to show
- If process died: relaunches fresh

### Legacy (DO NOT USE)

**onboard-toggle.sh** — old toggle for broken onboard. Still exists at
~/scripts/onboard-toggle.sh but is no longer wired to any button.

---

## 6. Auto-Switching (controller-profile-switcher.sh)

Polls the focused window class via `xdotool getactivewindow` + `xprop WM_CLASS`
every second and loads the matching profile:

| Window Class | Profile |
|-------------|---------|
| chrome, firefox, brave-browser, chromium, edge, opera, librewolf | ai-browser.amgp |
| mpv, vlc, kodi, hypnotix, stremio, smplayer, celluloid | ai-iptv.amgp |
| Anything else | ai-desktop.amgp |

Runs as systemd **user** service `antimicrox-autoload`.

Also sets up X11 keycodes for F13-F15 via xmodmap (not in default keymap):
```bash
xmodmap -e "keycode 202 = F13"
xmodmap -e "keycode 197 = F14"
xmodmap -e "keycode 217 = F15"
```

**AntiMicroX version pin:** 3.5.1 ONLY. 3.6.1 SIGSEGV on Qt6 6.2.4.

---

## 7. All File Paths

### Live Config (Local)

| Path | Purpose |
|------|---------|
| ~/.config/antimicrox/ai-desktop.amgp | Desktop profile (live, button 5 → toggle-slide-keyboard.sh) |
| ~/.config/antimicrox/ai-browser.amgp | Browser profile (live) |
| ~/.config/antimicrox/ai-iptv.amgp | IPTV/TV profile (live) |
| ~/.config/antimicrox/antimicrox_settings.ini | AntiMicroX settings |
| ~/scripts/toggle-slide-keyboard.sh | View button → toggle slide_keyboard.py |
| ~/scripts/slide_keyboard.py | Custom GTK floating on-screen keyboard |
| ~/scripts/onboard-toggle.sh | Legacy onboard toggle (deprecated, do not use) |
| ~/scripts/ptt_pynput.py | F13 listener → Groq Whisper → xdotool type |
| ~/scripts/controller-profile-switcher.sh | Window-focus auto-switcher |
| ~/scripts/hermes_tts_play.sh | TTS playback wrapper (mpv + lowpass=3000) |
| ~/.local/share/piper/en_US-joe-medium.onnx | Piper male voice (current default) |
| ~/scripts/voices/en_US-lessac-medium.onnx | Piper female voice (fine-tuned) |
| ~/scripts/voices/en_US-lessac-medium.onnx.json | Lessac voice config |
| ~/.config/systemd/user/antimicrox-autoload.service | Auto-switch service |
| ~/.config/systemd/user/ptt-pynput.service | PTT dictation listener service |
| ~/.config/systemd/user/voice-bridge.service | Voice bridge service |
| ~/.hermes/config.yaml | Hermes config (tts.provider=piper) |

### GitHub Repos

| Repo | URL | Contents |
|------|-----|----------|
| ai-controller-profile | https://github.com/ebey317/ai-controller-profile | Controller profiles, scripts, systemd units, this reference |
| master-ai-context | https://github.com/ebey317/master-ai-context | Cloud backup: CONFIGS/STT_WORKING_CONFIG_2026-06-18/ |

### Cloud Backup Location

All configs also backed up to:
`/home/elijah/projects/master-ai-context/CONFIGS/STT_WORKING_CONFIG_2026-06-18/`

Contains: ai-desktop.amgp, ai-controller.amgp, ai-browser.amgp, ai-iptv.amgp,
antimicrox_settings.ini, slide_keyboard.py, toggle-slide-keyboard.sh,
onboard-toggle.sh, ptt_pynput.py, ptt-pynput.service, voice_config.json,
controller-legend.py, controller-legend.service, README.md, .Xmodmap

---

## 8. Installation & Setup

```bash
git clone https://github.com/ebey317/ai-controller-profile
cd ai-controller-profile
bash scripts/install.sh
```

install.sh will:
1. Download AntiMicroX 3.5.1 AppImage to ~/scripts/
2. Copy profiles to ~/.config/antimicrox/
3. Install and enable antimicrox-autoload systemd service
4. Copy push-to-talk.sh to ~/scripts/

### Manual Setup (if install.sh not available)

```bash
# 1. Install AntiMicroX 3.5.1 (NOT 3.6.1)
# Ubuntu/Mint: apt install antimicrox
# Or download AppImage from https://github.com/AntiMicroX/antimicrox/releases

# 2. Copy profiles
cp profiles/ai-desktop.amgp ~/.config/antimicrox/
cp profiles/ai-browser.amgp ~/.config/antimicrox/
cp profiles/ai-iptv.amgp ~/.config/antimicrox/

# 3. Install scripts
cp scripts/toggle-slide-keyboard.sh ~/scripts/
cp scripts/slide_keyboard.py ~/scripts/
cp scripts/ptt_pynput.py ~/scripts/
cp scripts/controller-profile-switcher.sh ~/scripts/
chmod +x ~/scripts/toggle-slide-keyboard.sh ~/scripts/slide_keyboard.py
chmod +x ~/scripts/ptt_pynput.py ~/scripts/controller-profile-switcher.sh

# 4. Install systemd services
cp systemd/antimicrox-autoload.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now antimicrox-autoload.service
systemctl --user enable --now ptt-pynput.service
systemctl --user enable --now voice-bridge.service

# 5. Set Xbox headset mic as default source
pactl set-default-source alsa_input.usb-Microsoft_Controller_*
```

---

## 9. Troubleshooting & Pitfalls

### AntiMicroX version
- Use 3.5.1 ONLY. 3.6.1 SIGSEGV on Qt6 6.2.4.
- Always edit `ai-desktop.amgp` directly — the profile-switcher hardcodes this filename.

### Profile XML format
- Use `<gamecontroller configversion="19" appversion="3.5.1">` format.
- Legacy `<!DOCTYPE antimicrox-profile>` format silently fails to load.

### Onboard is broken
- Scanner mode stuck on backtick key (confirmed 2026-06-16).
- Use slide_keyboard.py instead (toggled by View button).

### Headset mic disappears at boot
- Two udev rules kill the headset mic at boot.
- FIX: move udev rules, usbreset, restore.
- `usbreset` WITHOUT moving rules = card 2 vanishes.

### pynput vs evdev
- AntiMicroX holds exclusive EVIOCGRAB on controller.
- Raw evdev reader gets zero events.
- pynput reads synthesized F13 keypress — no grab conflict.

### F13 not in default X11 keymap
- controller-profile-switcher.sh adds F13-F15 via xmodmap at startup.
- F13 = keycode 202, F14 = keycode 197, F15 = keycode 217.

### Piper voice json missing
- ~/.local/share/piper/en_US-lessac-medium.json is 0 bytes.
- Real config at ~/scripts/voices/en_US-lessac-medium.onnx.json.
- Piper needs the .json next to the .onnx — copy it if switching voices.

### Hermes TTS does not auto-play
- Hermes CLI generates audio but does NOT play it.
- Playback is per-utterance via ~/scripts/hermes_tts_play.sh → mpv.

### Cloud backup was stale
- As of 2026-06-18, cloud ai-desktop.amgp had wrong button mappings
  (View=Space, phantom button 19, wrong stick assignments).
- Fixed in commit 5b4cdfb — cloud now matches local.

---

## 10. Systemd Services

### antimicrox-autoload.service
- Type: simple
- ExecStart: /bin/bash ~/scripts/controller-profile-switcher.sh
- ExecStopPost: pkill -f antimicrox.AppImage
- Restart: on-failure
- After: graphical-session.target

### ptt-pynput.service
- Type: simple
- ExecStart: /usr/bin/python3 ~/scripts/ptt_pynput.py
- Restart: on-failure
- After: graphical-session.target, voice-bridge.service
- Environment: DISPLAY=:0

### voice-bridge.service
- ExecStart: claf venv python3 voice_bridge.py
- Restart: on-failure
- After: claf.service
- Port: 8002
- TTS: Edge TTS (en-US-AriaNeural, -22Hz pitch, +12% rate) via edge-tts CLI
- Fallback: spd-say if Edge TTS unavailable
- Playback: ~/scripts/hermes_tts_play.sh → mpv lowpass=3000
- Updated 2026-06-18: _speak() now uses Edge TTS instead of Piper for spoken responses

---

## 11. Reload Cheat Sheet

```bash
# Restart the whole stack
systemctl --user daemon-reload
systemctl --user restart antimicrox-autoload
systemctl --user restart voice-bridge
systemctl --user restart ptt-pynput

# Reload one profile manually
pkill -f antimicrox
DISPLAY=:0 setsid antimicrox --profile ~/.config/antimicrox/ai-desktop.amgp --tray &

# Toggle keyboard manually
bash ~/scripts/toggle-slide-keyboard.sh

# Test TTS
echo "test" | piper --model ~/.local/share/piper/en_US-joe-medium.onnx --output_file /tmp/test.wav
mpv --af=lowpass=f=3000 /tmp/test.wav

# Test voice dictation
# Hold RT, speak, release — text appears at cursor
```

---

## 12. Keycodes Reference

| Key | Qt Code | Key | Qt Code |
|-----|---------|-----|---------|
| Escape | 0x1000000 | Tab | 0x1000001 |
| Backspace | 0x1000003 | Return | 0x1000004 |
| Delete | 0x1000007 | Left | 0x1000012 |
| Up | 0x1000013 | Right | 0x1000014 |
| Down | 0x1000015 | PageUp | 0x1000016 |
| PageDown | 0x1000017 | Shift | 0x1000020 |
| Ctrl | 0x1000021 | Meta/Super | 0x1000022 |
| Alt | 0x1000023 | F1 | 0x1000030 |
| F13 (PTT) | 0x100003c | Space | 0x20 |
| Letters | 0x41–0x5A | Mouse left | code=1 |
| Mouse right | code=2 | Mouse middle | code=3 |

F-keys count up from F1: F5 = 0x1000034, F13 = 0x100003c.

---

**End of AI Controller Complete Reference.**
**Maintained by: Madam Mary (Sovereign Brain)**
**Repository: https://github.com/ebey317/ai-controller-profile**