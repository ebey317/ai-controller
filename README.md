# AI Controller Profile

**Run a full computer from a couch using only a controller and a mic.**

No keyboard. No mouse. Just talk and point.

---

## What This Is

Three pre-built controller profiles that cover every mode:

| Profile | When It Activates | Based On |
|---|---|---|
| **Desktop** | Always (fallback) | Steam Deck Lizard Mode |
| **Browser** | Chrome / Firefox / Edge in focus | Xbox Dashboard |
| **IPTV** | MPV / Kodi / VLC / Hypnotix in focus | Vizio remote + standard IPTV |

Plug in Xbox or PlayStation controller → profiles load automatically.  
Works out of the box on Linux. macOS partial. Windows manual.

---

## Quick Start

```bash
git clone https://github.com/yourusername/ai-controller-profile
cd ai-controller-profile
bash install.sh
```

Done. Plug in controller. antimicroX loads the right profile automatically.

---

## Button Layout

### Desktop (Universal)
```
Left Stick  → Mouse cursor
Right Stick → Scroll
D-Pad       → Arrow keys
A           → Left click / Enter
B           → Escape
X           → Copy (Ctrl+C)
Y           → Paste (Ctrl+V)
LB / RB     → Switch workspace
LT          → Right click
RT          → *** PUSH TO TALK *** (see config below)
START       → App launcher (Super key)
BACK        → Alt+Tab
L3          → Middle click
R3          → Close window (Ctrl+W)
```

### Browser (Chrome / Edge / Firefox)
```
Left Stick  → Mouse cursor (fine control for links)
Right Stick → Scroll (up/down/left/right)
D-UP/DOWN   → Tab between links (Tab / Shift+Tab)
D-LEFT      → Page back
D-RIGHT     → Page forward
A           → Click
B           → Back
X           → Reload (F5)
Y           → New tab (Ctrl+T)
LB / RB     → Previous / Next tab
LT          → Right click (context menu)
RT          → PUSH TO TALK
START       → Address bar — speak your URL (Ctrl+L)
BACK        → Bookmark (Ctrl+D)
L3          → Open link in new tab (middle click)
R3          → Close tab (Ctrl+W)
```

### IPTV (MPV / Kodi / VLC / Hypnotix)
```
Left Stick  → Menu navigation
Right Stick → Seek within stream
D-UP        → Previous channel
D-DOWN      → Next channel
D-LEFT      → Volume down
D-RIGHT     → Volume up
A           → Play / Pause (Space)
B           → Stop / Back (Escape)
X           → Show info (i)
Y           → Fullscreen (f)
LB          → Jump back 10 channels (PageUp)
RB          → Jump forward 10 channels (PageDown)
LT          → Rewind 30s
RT          → Fast forward 30s
START       → Playlist / menu (m)
BACK        → EPG / guide (g)
L3          → Mute
R3          → Subtitles
```

---

## Push-to-Talk Setup

**Edit `scripts/push-to-talk.sh` — top section only:**

```bash
# Your mic button
MIC_TRIGGER="F13"              # Default: RT on controller maps to F13
# MIC_TRIGGER="XF86AudioMicMute"  # ← use this if your headset has an inline mic button

# What happens after you speak
SEND_BEHAVIOR="release"   # sends automatically when you let go
# SEND_BEHAVIOR="review"  # shows you the text first — press Enter to send, ESC to cancel
```

### To find your headphone mic button:
```bash
bash scripts/push-to-talk.sh --detect-button
# Press your headphone button when prompted — it prints the key name
# Paste that name into MIC_TRIGGER above
```

---

## Requirements

- Linux (Ubuntu 20.04+, Mint, PopOS, etc.)
- [antimicroX](https://github.com/AntiMicroX/antimicrox) (installed by `install.sh`)
- Xbox One/Series or PlayStation 4/5 controller (USB or Bluetooth)
- `arecord`, `curl`, `xdotool` (installed by `install.sh`)

---

## Transferring to Another Machine

```bash
# From any machine with this repo:
bash install.sh
# That's it. Profiles copy to ~/.config/antimicrox/ and service enables.
```

With Tailscale: `scp -r ai-controller-profile/ remote-machine:~/projects/`

---

## Credits

Button layouts stolen (with respect) from:
- Steam Deck Lizard Mode (Valve)
- Xbox Dashboard navigation design (Microsoft)  
- Vizio remote layout (industry IPTV standard)
- MPV `--input-gamepad=yes` native gamepad mode

---

## License

MIT — free to use, free to fork, free to sell your own polished version.
