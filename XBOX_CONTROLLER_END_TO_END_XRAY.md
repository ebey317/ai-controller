# 🎮 Xbox Controller → Linux Desktop: End-to-End X-Ray

**Machine:** Elijah (`elijah-MS-7B86`)  
**Controller:** Microsoft Xbox Wireless Controller model 1914 (`045e:0b12`)  
**Kernel:** Linux 5.15.0-181-generic  
**xone driver:** v0.3-59-g3484f60 (DKMS)  
**Last verified:** 2026-06-25  
**Repo:** https://github.com/ebey317/ai-controller-profile

This document is the complete circuit diagram of the working system: every hardware path, kernel module, udev rule, systemd service, script, PulseAudio device, and X11 event — and exactly how they connect.

---

## 1. Executive Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Xbox Wireless Controller (USB)                                             │
│  045e:0b12 — bus 1 / port 4 (root hub usb1)                                 │
└─────────────────────┬───────────────────────────────────────────────────────┘
                      │ USB full-speed
                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Kernel: xone-wired → xone-gip                                              │
│  ├─ xone-gip-gamepad  → /dev/input/js0  + /dev/input/event23               │
│  └─ xone-gip-headset  → ALSA card 2 "Microsoft Xbox Headset"               │
└─────────────────────┬───────────────────────────────────────────────────────┘
                      │
        ┌─────────────┴──────────────┐
        ▼                            ▼
┌───────────────┐          ┌──────────────────────┐
│ AntiMicroX    │          │ PulseAudio           │
│ (SDL joystick)│          │ ├─ source: mono mic  │
│ maps buttons/ │          │ └─ sink: stereo out  │
│ axes to XTest │          │   (combined profile) │
└───────┬───────┘          └──────────┬───────────┘
        │                             │
        ▼                             ▼
┌───────────────┐          ┌──────────────────────┐
│ ptt_pynput.py │          │ voice_bridge.py      │
│ listens for   │          │ :8002                │
│ F13 (RT)      │          │ Groq Whisper STT     │
└───────┬───────┘          │ Edge TTS response    │
        │                  └──────────┬───────────┘
        │                             │
        └──────────┬──────────────────┘
                   ▼
        ┌──────────────────────┐
        │ xdotool / clipboard  │
        │ types transcript     │
        │ into focused window  │
        └──────────────────────┘
```

---

## 2. Hardware / USB Layer

### 2.1 Device identity
```bash
lsusb | grep -iE "microsoft|xbox"
# Bus 001 Device 006: ID 045e:0b12 Microsoft Corp. Xbox Wireless Controller (model 1914)
```

### 2.2 USB topology
```
usb1 (root hub, xhci_hcd)
  └── 1-4  ← controller physical port
        ├── 1-4:1.0  → xone-wired (gamepad + GIP bus)
        ├── 1-4:1.1  → disabled by udev (authorized=0)
        └── 1-4:1.2  → GIP headset endpoint
```

### 2.3 Power management (critical for stability)
| Node | Current value | Meaning |
|------|--------------|---------|
| `/sys/bus/usb/devices/1-4/power/control` | `on` | Controller never autosuspends |
| `/sys/bus/usb/devices/1-4/power/wakeup` | `disabled` | Controller can't wake hub |
| `/sys/bus/usb/devices/1-4/power/autosuspend_delay_ms` | `-1000` | Autosuspend disabled |
| `/sys/bus/usb/devices/1-4/power/runtime_enabled` | `forbidden` | Runtime PM disabled |
| `/sys/bus/usb/devices/usb1/power/control` | `on` | ✅ locked by udev rule |

The controller-side power fix persists via udev. The root hub (`usb1`) is now also locked `on` via `/etc/udev/rules.d/49-xbox-root-hub-no-autosuspend.rules`.

---

## 3. Kernel / Driver Layer

### 3.1 DKMS source and built modules
```
/usr/src/xone-v0.3-59-g3484f60/          # source tree
/var/lib/dkms/xone/v0.3-59-g3484f60/     # DKMS build metadata
/lib/modules/5.15.0-181-generic/updates/dkms/xone-*.ko
```

Built modules:
- `xone-dongle.ko`
- `xone-gip.ko`
- `xone-gip-chatpad.ko`
- `xone-gip-gamepad.ko`
- `xone-gip-headset.ko`
- `xone-gip-madcatz-glam.ko`
- `xone-gip-madcatz-strat.ko`
- `xone-gip-pdp-jaguar.ko`
- `xone-wired.ko`

### 3.2 Module load state
```
xone_gip_headset       20480  3
xone_gip_gamepad       16384  0
xone_wired             20480  0
xone_gip               49152  3 xone_gip_gamepad,xone_wired,xone_gip_headset
ff_memless             24576  1 xone_gip_gamepad
ecdh_generic           16384  1 xone_gip
```

### 3.3 Module autoload / blacklist
- `/etc/modules-load.d/xone-headset.conf` → `xone-gip-headset`
- `/etc/modprobe.d/xone-blacklist.conf` → `blacklist xpad`

### 3.4 Input subsystem nodes
```
/dev/input/js0                                      → joystick interface
/dev/input/event23                                  → evdev interface
/sys/class/input/event23/device/name                → "Microsoft Xbox Controller"
/dev/input/by-id/usb-Microsoft_Controller_...-event-joystick
/dev/input/by-path/pci-0000:03:00.0-usb-0:4:1.0-event-joystick
```

### 3.5 ALSA headset card
```
/proc/asound/cards
card 2: Headset [Microsoft Xbox Headset], device 0
```

---

## 4. udev Rules (Persistence Layer)

All system rules live in `/etc/udev/rules.d/`:

| Rule file | Purpose |
|-----------|---------|
| `49-xbox-root-hub-no-autosuspend.rules` | Locks root hub `usb1` `power/control=on` |
| `50-xbox-controller-no-autosuspend.rules` | Sets controller `power/control=on`, `power/wakeup=disabled` |
| `50-xbox-controller-stable.rules` | Also sets `power/control=on`, `power/autosuspend=-1` |
| `50-xbox-controller-no-headset.rules` | Disables interface `1-4:1.1` (`authorized=0`) |
| `50-xbox-led.rules` | Sets controller LED |
| `50-xbox-mic-default.rules` | Sets Xbox mic as default source |
| `50-xbox-usb-power.rules` | Additional power policy |
| `51-xbox-headset-unbind.rules` | Unbinds interface `1.1` from `xone-wired` |
| `52-xbox-headset-wake.rules` | Triggers `xbox-headset-wake.service` on connect |

Project copies:
- `/home/elijah/projects/ai-controller-profile/udev/52-xbox-headset-wake.rules`
- Installed to `/etc/udev/rules.d/52-xbox-headset-wake.rules`

### 4.1 Critical rule for headset auto-wake
```udev
# /etc/udev/rules.d/52-xbox-headset-wake.rules
ACTION=="add", SUBSYSTEM=="usb", ATTRS{idVendor}=="045e", ATTRS{idProduct}=="0b12", TAG+="systemd", RUN+="/bin/systemctl --no-block start xbox-headset-wake.service"
```

### 4.2 Power rule
```udev
# /etc/udev/rules.d/50-xbox-controller-no-autosuspend.rules
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="045e", ATTR{idProduct}=="0b12", ATTR{power/control}="on", ATTR{power/wakeup}="disabled"
```

---

## 5. Headset Auto-Wake Mechanism

### 5.1 Problem
The controller announces its headset over GIP only on an analog insertion edge. If it reconnects with the 3.5mm plug already seated, no edge fires, so `xone-gip-headset` never creates the capture device. The user previously had to physically reseat the plug.

### 5.2 Solution
`xbox-headset-wake.sh` performs a "software reseat" on controller connect.

Files:
- `/usr/local/bin/xbox-headset-wake.sh` (installed from repo)
- `/etc/systemd/system/xbox-headset-wake.service`
- `/etc/udev/rules.d/52-xbox-headset-wake.rules`
- Repo source: `/home/elijah/projects/ai-controller-profile/scripts/xbox-headset-wake.sh`

### 5.3 Algorithm
1. Loop guard: don't run more than once per 30 seconds.
2. Wait 3 seconds for GIP stack to settle.
3. If `/proc/asound/cards` already contains "Xbox Headset", exit.
4. Locate controller USB device by `045e:0b12`.
5. Escalating kicks:
   - Reload `xone_gip_headset` module
   - Toggle USB `authorized` off/on to simulate disconnect/reconnect
   - Re-check for headset card

### 5.4 Service unit
```ini
[Unit]
Description=Wake Xbox controller headset mic on connect (software reseat)

[Service]
Type=oneshot
ExecStart=/usr/local/bin/xbox-headset-wake.sh
TimeoutStartSec=30
```

---

## 6. Input / AntiMicroX / PTT Layer

### 6.1 AntiMicroX profile files
Active directory: `/home/elijah/.config/antimicrox/`

| Profile file | Status |
|--------------|--------|
| `ai-desktop.amgp` | Installed by installer; documented canonical profile |
| `ai-desktop-final.amgp` | Variant |
| `good_1n.gamecontroller.amgp` | **Currently loaded at runtime** |
| `linux_workflow.amgp` | Variant |

Repo source profiles: `/home/elijah/projects/ai-controller-profile/profiles/`

### 6.2 Currently running AntiMicroX command
```
/usr/bin/antimicrox --profile /home/elijah/.config/antimicrox/good_1n.gamecontroller.amgp --tray --eventgen xtest
```

### 6.3 Right Trigger → F13 mapping (currently active)
```xml
<!-- good_1n.gamecontroller.amgp -->
<trigger index="6">
  <triggerbutton index="2">
    <slots>
      <slot>
        <code>0xFFCA</code>
        <mode>keyboard</mode>
      </slot>
    </slots>
  </triggerbutton>
</trigger>
```
`0xFFCA` = F13 keysym.

### 6.4 F13 keycode persistence
File: `/home/elijah/.Xmodmap`
```xmodmap
keycode 191 = F13
keycode 202 = F13
keycode 197 = F14
keycode 217 = F15
keycode 219 = F16
keycode 222 = F17
keycode 230 = F18
```

Loaded by:
- `~/.xsessionrc` at X session start
- `~/.config/autostart/fix-f13-keymap.desktop` after keyboard daemon resets
- `ptt-pynput.service` `ExecStartPre=-/usr/bin/xmodmap %h/.Xmodmap`

### 6.5 PTT signal flow
```
Hold RT
  → AntiMicroX emits F13 key press
  → ptt_pynput.py pynput listener catches Key.f13
  → press:
      - pkill any playing TTS (ai_controller_tts)
      - inject leading space via xdotool
      - start parec from default Xbox mic source
  → release:
      - stop parec
      - wrap raw PCM to WAV
      - POST http://localhost:8002/voice?mode=transcribe_only
      - receive transcript JSON
      - apply vocabulary + style mode
      - type/clipboard/browser-inject result
```

### 6.6 PTT script
- Active: `/home/elijah/ai-controller/scripts/ptt_pynput.py`
- Service: `/home/elijah/.config/systemd/user/ptt-pynput.service`

Capture command:
```python
['parec', '--rate', '24000', '--channels', '1', '--format', 's16le', '--raw']
```

### 6.7 Style / input state files
- `/home/elijah/.config/ai-controller/ptt_mode` → current dictation style (`casual`, `pro`, `bubbly`, `bold`, `big`)
- `/home/elijah/.config/ai-controller/ai_controller_input_target` → `type` or `clipboard`

Note: `ptt-pynput.service` resets `ptt_mode` to `pro` on every service start.

---

## 7. Audio / PulseAudio Layer

### 7.1 Controller audio card
```
Name: alsa_card.usb-Microsoft_Controller_3039373130383038333134313433-00
Driver: module-alsa-card.c
alsa.driver_name: xone_gip_headset
```

### 7.2 Available profiles
| Profile | Sinks | Sources |
|---------|-------|---------|
| `input:mono-fallback` | 0 | 1 (mic) |
| `output:stereo-fallback` | 1 (headphones) | 0 |
| `output:stereo-fallback+input:mono-fallback` | 1 | 1 |
| `off` | 0 | 0 |

### 7.3 Current live state
```
Active Profile: input:mono-fallback
Default Source: alsa_input.usb-Microsoft_Controller_3039373130383038333134313433-00.mono-fallback
Default Sink:   alsa_output.pci-0000_28_00.4.analog-stereo   (PC speakers)
```

The card is currently input-only, so TTS plays through PC speakers, not the controller headset.

### 7.4 Audio scripts
| Script | Purpose |
|--------|---------|
| `/home/elijah/ai-controller/scripts/reset-controller-audio.sh` | Software unplug/replug via PulseAudio profile cycle |
| `/home/elijah/ai-controller/scripts/lock_audio_routing.sh` | Force combined profile + set Xbox sink/source as default |
| `/home/elijah/scripts/controller_audio_toggle.sh` | Toggle default output between controller headset and PC |
| `/home/elijah/ai-controller/scripts/ai-audio-test.sh` | 3-second loopback test |
| `/home/elijah/ai-controller/scripts/hermes_tts_play.sh` | Play TTS via mpv with sink detection |
| `/home/elijah/scripts/set_xbox_mic_default.sh` | Set Xbox mic as default source |

### 7.5 Reset script logic
```bash
WANT="${CONTROLLER_AUDIO_PROFILE:-combined}"
pactl set-card-profile "$CARD" off
sleep 0.7
pactl set-card-profile "$CARD" "$ON_PROFILE"
# set default source/sink
```

Use `CONTROLLER_AUDIO_PROFILE=input` for mic-only (avoids `-28` ENOSPC wedge).
Use `CONTROLLER_AUDIO_PROFILE=combined` for headset speakers + mic.

### 7.6 TTS playback path
```
voice_bridge.py _speak()
  → edge-tts → temporary MP3
  → hermes_tts_play.sh
  → mpv --no-video --audio-device=pulse/<sink>
  → PulseAudio sink
```

If the Xbox sink is available, audio routes to the controller headphones. Otherwise it falls back to the current default sink.

---

## 8. Voice Bridge / STT Layer

### 8.1 Service
- File: `/home/elijah/ai-controller/scripts/voice_bridge.py`
- Service: `/home/elijah/.config/systemd/user/voice-bridge.service`
- Endpoint: `http://127.0.0.1:8002`

### 8.2 Config
`/home/elijah/.config/ai-controller/config.env`:
```ini
AI_CONTROLLER_DIR=/home/elijah/ai-controller
GROQ_API_KEY=<redacted>
AUDIO_INPUT=alsa_input.usb-Microsoft_Controller_3039373130383038333134313433-00.mono-fallback
AUDIO_OUTPUT=alsa_output.usb-Microsoft_Controller_3039373130383038333134313433-00.stereo-fallback
```

### 8.3 Request flow
```
POST /voice?mode=transcribe_only
  ← WAV bytes (24 kHz, mono, s16le)
  → Groq Whisper (whisper-large-v3-turbo)
  → transcript JSON
```

For full response mode:
```
POST /voice
  → Whisper transcript
  → Groq LLM (llama-3.3-70b-versatile)
  → Edge TTS (en-US-AriaNeural)
  → mpv playback
```

---

## 9. Systemd Services / Launchers

### 9.1 Core user services
All enabled and running:

| Service | ExecStart | Purpose |
|---------|-----------|---------|
| `antimicrox-autoload.service` | `controller-profile-switcher.sh` | Loads AntiMicroX + switches profiles |
| `ptt-pynput.service` | `ptt_pynput.py` | F13 listener + dictation |
| `voice-bridge.service` | `voice_bridge.py` | STT/TTS API server |
| `controller-legend.service` | `controller-legend.py` | Button-mapping HUD |
| `ai-slide-keyboard.service` | `slide_keyboard.py --show` | On-screen keyboard |

### 9.2 System service (udev-triggered)
- `xbox-headset-wake.service` — runs `xbox-headset-wake.sh` on controller connect

### 9.3 Launcher
- `/home/elijah/ai-controller/scripts/ai-controller-launcher.py`
- `/home/elijah/ai-controller/scripts/ai-controller-launcher.sh`
- Autostart: `/home/elijah/.config/autostart/ai-controller-launcher.desktop`

Launcher hard-coded service list:
```python
SERVICES = [
    "antimicrox-autoload.service",
    "voice-bridge.service",
    "ptt-pynput.service",
    "controller-legend.service",
    "ai-slide-keyboard.service",
]
```

### 9.4 Installer
- `/home/elijah/ai-controller/install.sh`
- Also: `/home/elijah/projects/ai-controller-profile/scripts/install.sh`

Recent installer additions (2026-06-25):
- F13-F18 keymap persistence (`~/.Xmodmap`, `~/.xsessionrc`, autostart desktop)
- `xone-gip-headset` module load at boot
- Headset auto-wake (udev rule + system service + script)

---

## 10. End-to-End Signal Trace: PTT Dictation

```
1. USER holds Right Trigger (RT)
   ↓
2. Controller HID report → /dev/input/event23
   ↓
3. xone-gip-gamepad → Linux input subsystem
   ↓
4. AntiMicroX reads SDL event, applies good_1n.gamecontroller.amgp
   ↓
5. AntiMicroX injects F13 key press via XTest
   ↓
6. ptt_pynput.py pynput listener sees Key.f13 press
   ↓
7. ptt_pynput.py:
      - kills playing TTS
      - types a leading space
      - starts parec from default source (Xbox mono mic)
   ↓
8. Audio flows: controller 3.5mm mic → xone-gip-headset → ALSA → PulseAudio → parec
   ↓
9. USER releases RT
   ↓
10. AntiMicroX injects F13 key release
    ↓
11. ptt_pynput.py stops parec, wraps PCM to WAV
    ↓
12. POST http://localhost:8002/voice?mode=transcribe_only
    ↓
13. voice_bridge.py sends WAV to Groq Whisper
    ↓
14. Groq returns transcript
    ↓
15. voice_bridge.py returns JSON: {"transcript":"..."}
    ↓
16. ptt_pynput.py applies vocabulary + style mode
    ↓
17. xdotool types (or clipboard injects) the text into the focused window
```

---

## 11. End-to-End Signal Trace: TTS Response

```
1. voice_bridge.py decides to speak
   ↓
2. edge-tts generates MP3 (en-US-AriaNeural, pitch -22Hz, rate +4%)
   ↓
3. hermes_tts_play.sh receives MP3 path
   ↓
4. mpv --no-video --audio-device=pulse/<sink>
   ↓
5. If controller card is in combined profile:
      → sink = alsa_output.usb-Microsoft_Controller_...stereo-fallback
      → audio plays in controller headphones
   Else:
      → fallback to default sink (PC speakers)
```

---

## 12. Persistence Mechanisms

### 12.1 What survives reboot
| Mechanism | File | Effect |
|-----------|------|--------|
| udev | `/etc/udev/rules.d/50-xbox-controller-no-autosuspend.rules` | Controller power `on`, wakeup `disabled` |
| udev | `/etc/udev/rules.d/52-xbox-headset-wake.rules` | Auto-wake headset on connect |
| modules-load | `/etc/modules-load.d/xone-headset.conf` | Headset module loads at boot |
| modprobe | `/etc/modprobe.d/xone-blacklist.conf` | `xpad` blacklisted |
| X session | `~/.Xmodmap` + `~/.xsessionrc` | F13-F18 keycodes loaded at login |
| autostart | `~/.config/autostart/fix-f13-keymap.desktop` | Re-applies keymap after desktop daemon resets |
| systemd user | `~/.config/systemd/user/*.service` | Core services auto-start |
| autostart | `~/.config/autostart/ai-controller-launcher.desktop` | Launcher GUI auto-starts |

### 12.2 What does NOT survive reboot
| Item | Why | Fix needed |
|------|-----|------------|
| PulseAudio card profile | Defaults to `input:mono-fallback` | Run `reset-controller-audio.sh` at boot |
| Default sink | Falls back to PC speakers | Run `lock_audio_routing.sh` at boot |
| `ptt_mode` style | Service resets to `pro` on restart | Remove reset or persist user choice |

---

## 13. File Inventory (Absolute Paths)

### System-level files
```
/etc/modules-load.d/xone-headset.conf
/etc/modprobe.d/xone-blacklist.conf
/etc/udev/rules.d/49-xbox-root-hub-no-autosuspend.rules
/etc/udev/rules.d/50-xbox-controller-no-autosuspend.rules
/etc/udev/rules.d/50-xbox-controller-no-headset.rules
/etc/udev/rules.d/50-xbox-controller-stable.rules
/etc/udev/rules.d/50-xbox-led.rules
/etc/udev/rules.d/50-xbox-mic-default.rules
/etc/udev/rules.d/50-xbox-usb-power.rules
/etc/udev/rules.d/51-xbox-headset-unbind.rules
/etc/udev/rules.d/52-xbox-headset-wake.rules
/etc/systemd/system/xbox-headset-wake.service
/usr/local/bin/xbox-headset-wake.sh
```

### Active install tree
```
/home/elijah/ai-controller/
├── install.sh
├── ai-controller-launcher.desktop
├── scripts/
│   ├── ai-controller-launcher.py
│   ├── ai-controller-launcher.sh
│   ├── controller-profile-switcher.sh
│   ├── controller-legend.py
│   ├── slide_keyboard.py
│   ├── ptt_pynput.py
│   ├── voice_bridge.py
│   ├── voice_manager.py
│   ├── reset-controller-audio.sh
│   ├── lock_audio_routing.sh
│   ├── ai-audio-test.sh
│   ├── hermes_tts_play.sh
│   ├── start-all.sh
│   └── xbox-headset-wake.sh
├── systemd/
│   ├── antimicrox-autoload.service
│   ├── ptt-pynput.service
│   ├── voice-bridge.service
│   ├── controller-legend.service
│   ├── ai-slide-keyboard.service
│   └── xbox-headset-wake.service
└── profiles/
    ├── ai-desktop.amgp
    ├── ai-browser.amgp
    ├── ai-iptv.amgp
    └── good_1n.gamecontroller.amgp
```

### Runtime config/state
```
/home/elijah/.config/antimicrox/
/home/elijah/.config/ai-controller/
/home/elijah/.config/ai-controller/config.env
/home/elijah/.config/ai-controller/ptt_mode
/home/elijah/.config/ai-controller/ai_controller_input_target
/home/elijah/.config/ai-controller/ai_controller_voice
/home/elijah/.config/systemd/user/
/home/elijah/.Xmodmap
/home/elijah/.xsessionrc
/home/elijah/.config/autostart/fix-f13-keymap.desktop
/home/elijah/.config/autostart/ai-controller-launcher.desktop
/home/elijah/.controller_current_profile
```

---

## 14. Current Live State Snapshot (2026-06-25)

```
Hostname:        elijah-MS-7B86
Controller:      Microsoft Xbox Wireless Controller 045e:0b12 on usb1/1-4
Kernel modules:  xone_wired, xone_gip, xone_gip_gamepad, xone_gip_headset loaded
Input nodes:     /dev/input/js0, /dev/input/event23 present
AntiMicroX:      running, profile = good_1n.gamecontroller.amgp
PTT listener:    running, listening for F13
Voice bridge:    running on 127.0.0.1:8002
PulseAudio card: alsa_card.usb-Microsoft_Controller_... active profile input:mono-fallback
Default source:  Xbox mono mic
Default sink:    PC analog stereo
Root hub power:  on (locked via udev)
```

---

## 15. Known Gaps / Next Steps

1. **Audio output not enabled by default** — card boots to `input:mono-fallback`. Add a systemd user unit that runs `reset-controller-audio.sh` (with `CONTROLLER_AUDIO_PROFILE=combined`) after PulseAudio starts.

3. **Active AntiMicroX profile mismatch** — live profile is `good_1n.gamecontroller.amgp`, not the documented `ai-desktop.amgp`. Align installer, switcher, and legend.

4. **ptt_mode reset on restart** — `ptt-pynput.service` resets style to `pro` on every start. Persist user choice or remove the reset.

5. **Multiple source trees** — drift between `/home/elijah/ai-controller/`, `/home/elijah/scripts/`, `/home/elijah/projects/ai-controller-profile/`, and `/home/elijah/projects/-AI-controller./`. Consolidate on one canonical tree.

6. **Conflicting headset rules** — `50-xbox-controller-no-headset.rules` disables interface `1.1` while the wake mechanism tries to use it. Verify this is intentional and stable.

7. **dmesg `-28` errors still occur** — despite power fixes. Root-hub persistence + combined-profile stability may reduce them further; otherwise investigate USB port/cable/firmware.

---

## 16. Quick Diagnostic Commands

```bash
# Controller present?
lsusb | grep -iE "microsoft|xbox"

# Driver loaded?
lsmod | grep xone

# Input nodes?
ls -la /dev/input/js0 /dev/input/event23
cat /sys/class/input/event23/device/name

# Power state?
cat /sys/bus/usb/devices/1-4/power/control
cat /sys/bus/usb/devices/usb1/power/control

# Audio card?
pactl list cards | grep -A2 "Active Profile"
pactl info | grep -E "Default Sink|Default Source"

# Services?
systemctl --user status antimicrox-autoload ptt-pynput voice-bridge

# Recent kernel errors?
dmesg | grep -iE "xone|gip|protocol error|get buffer failed" | tail -20

# Test PTT audio path
bash /home/elijah/ai-controller/scripts/ai-audio-test.sh
```

---

**Document generated:** 2026-06-25  
**Maintained by:** Madam Mary (Sovereign Brain)  
**Repo:** https://github.com/ebey317/ai-controller-profile
