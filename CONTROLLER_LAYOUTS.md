# Xbox Controller → Full Desktop / Browser / TV / Voice Control

**Status: WORKING — 2026-06-14**

One Xbox Series X controller drives the entire Linux desktop: mouse, keyboard,
web browsing, IPTV/TV remote, AND voice dictation — with profiles that
**auto-switch based on the focused window**. No keyboard or mouse required.

---

## THE BREAKTHROUGH (why it works now)

AntiMicroX 3.5.1's native profile format is:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<gamecontroller configversion="19" appversion="3.5.1">
```

The format that **fights you all night and silently fails to load** is the
legacy antimicro 2.x format:

```xml
<!DOCTYPE antimicrox-profile>
<antimicrox-profile version="2">
```

**Use the `gamecontroller configversion="19"` schema. Always.** It is what the
GUI saves and what the engine reliably reads.

### Keycodes are Qt key codes (hex)

Letters are **uppercase ASCII** (`V` = `0x56`). Special keys are Qt codes:

| Key | Code | Key | Code |
|-----|------|-----|------|
| Escape | `0x1000000` | Tab | `0x1000001` |
| Backspace | `0x1000003` | Return | `0x1000004` |
| Enter (keypad) | `0x1000005` | Delete | `0x1000007` |
| Left | `0x1000012` | Up | `0x1000013` |
| Right | `0x1000014` | Down | `0x1000015` |
| PageUp | `0x1000016` | PageDown | `0x1000017` |
| Shift | `0x1000020` | Ctrl | `0x1000021` |
| Meta/Super | `0x1000022` | Alt | `0x1000023` |
| F1 | `0x1000030` | F13 (PTT) | `0x100003c` |
| Space | `0x20` | Letter X | `0x41`–`0x5A` |

F-keys count up from F1: F5 = `0x1000034`, F13 = `0x100003c`.

Mouse uses `<mode>mousebutton</mode>` with `<code>1</code>` (left), `2` (right),
`3` (middle), `4`/`5` (wheel). Cursor uses `<mode>mousemovement</mode>`.

---

## BUTTON LAYOUTS

| Button | Desktop | Browser | IPTV / TV |
|--------|---------|---------|-----------|
| **A** | Left click (hold = onboard kbd) | Click | Play/Pause (Space) |
| **B** | Escape (back out) | Back (Alt+Left) | Stop (Escape) |
| **X** | Backspace (delete char) | Reload (F5) | Info (i) |
| **Y** | Paste (Ctrl+V) | New tab (Ctrl+T) | Fullscreen (f) |
| **LB** | Alt+Tab | Prev tab (Ctrl+Shift+Tab) | Channel page ↑ (PgUp) |
| **RB** | Right click | Next tab (Ctrl+Tab) | Channel page ↓ (PgDn) |
| **LT** | Return (submit) | Right click | Rewind (Left) |
| **RT** | **DICTATION (F13)** | **DICTATION (F13)** | Fast-fwd (Right) |
| **D-pad** | Arrows / PgUp-Dn | Tab nav / Back-Fwd | Channel / Volume |
| **L-stick** | Mouse cursor | Mouse cursor | Mouse cursor |
| **R-stick** | Scroll wheel | Scroll wheel | Scroll wheel |
| **Back/View** | Delete | Bookmark (Ctrl+D) | Guide/EPG (g) |
| **Start/Menu** | Middle click | Address bar (Ctrl+L) | Menu (m) |
| **L3** | F13 (PTT) | Middle click | Mute (m) |
| **R3** | Super | Close tab (Ctrl+W) | Subtitles (j) |

---

## VOICE DICTATION (RT trigger)

Hold **RT** → speak → release → your words type into the focused window.

**Pipeline:**
```
RT (controller) → AntiMicroX maps to F13
  → push-to-talk.sh listens for F13 (pynput, no exclusive grab)
  → arecord captures Xbox controller 3.5mm mic (PulseAudio default source)
  → Groq Whisper (whisper-large-v3-turbo) transcribes
  → xdotool types the text into the focused window
```

### Components
- **Mic:** Xbox controller 3.5mm headset jack, exposed via the `xone` kernel
  driver (medusalix/xone, DKMS). Shows as ALSA `card: Microsoft Xbox Headset`
  and PulseAudio source `alsa_input.usb-Microsoft_Controller_*`.
- **STT:** `scripts/voice_bridge.py` — FastAPI on `:8002`, Groq Whisper.
  `mode=transcribe_only` returns raw text for dictation.
- **PTT listener:** `scripts/push-to-talk.sh` — pynput F13 listener (NOT evdev:
  AntiMicroX holds an exclusive `EVIOCGRAB` on the controller, so a raw evdev
  reader gets no events; pynput reads the synthesized F13 keypress instead).

### Why pynput, not evdev
AntiMicroX grabs the controller device exclusively. A second evdev reader opens
the device but receives zero events. Since AntiMicroX already translates RT→F13,
listen for **F13 at the keyboard layer** with pynput — no grab conflict.

### Audio gotchas
- Xbox jack enumerates only when the `xone-gip-headset` module is loaded AND
  something is plugged into the controller jack.
- PulseAudio claims the device on connect → set it as default source:
  `pactl set-default-source alsa_input.usb-Microsoft_Controller_*`
- Persisted in `~/.config/pulse/default.pa` (keeps analog headphones as the
  default *sink* so TTS/video audio doesn't get hijacked to the controller).

---

## AUTO-SWITCHING

`~/scripts/controller-profile-switcher.sh` polls the focused window class
(via `xdotool getactivewindow` + `xprop WM_CLASS`) every second and loads the
matching profile:

- Browser (chrome/firefox/brave/edge/opera) → `ai-browser.amgp`
- Media (mpv/vlc/kodi/hypnotix/stremio/smplayer/celluloid) → `ai-iptv.amgp`
- Anything else → `ai-desktop.amgp`

Runs as the systemd **user** service `antimicrox-autoload`.

```bash
systemctl --user daemon-reload
systemctl --user restart antimicrox-autoload
systemctl --user enable antimicrox-autoload   # autostart on login
```

---

## FILES

| Path | Purpose |
|------|---------|
| `~/.config/antimicrox/ai-desktop.amgp` | Desktop profile (live) |
| `~/.config/antimicrox/ai-browser.amgp` | Browser profile (live) |
| `~/.config/antimicrox/ai-iptv.amgp` | IPTV/TV profile (live) |
| `~/scripts/controller-profile-switcher.sh` | Window-focus auto-switcher |
| `~/scripts/antimicrox.AppImage` | AntiMicroX 3.5.1 binary |
| `scripts/push-to-talk.sh` | F13 → Whisper → xdotool dictation |
| `scripts/voice_bridge.py` | Groq Whisper STT bridge (:8002) |
| `~/.config/systemd/user/antimicrox-autoload.service` | Auto-switch service |
| `~/.config/systemd/user/voice-bridge.service` | Voice bridge service |
| `~/.config/pulse/default.pa` | Mic source / speaker sink persistence |
| `profiles/*.amgp` | Repo copies (source of truth) |

---

## RELOAD CHEAT SHEET

```bash
# Reload one profile manually
pkill -f antimicrox
DISPLAY=:0 setsid ~/scripts/antimicrox.AppImage \
    --profile ~/.config/antimicrox/ai-desktop.amgp --tray &

# Restart the whole auto-switching stack
systemctl --user daemon-reload
systemctl --user restart antimicrox-autoload
systemctl --user restart voice-bridge

# Start dictation listener (if not autostarted)
bash ~/projects/ai-controller-profile/scripts/push-to-talk.sh
```
