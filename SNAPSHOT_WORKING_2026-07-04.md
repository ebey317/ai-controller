# AI Controller Working Snapshot — 2026-07-04
# =============================================
# This is the EXACT working configuration as verified by Elijah Wilkins.
# DO NOT CHANGE without explicit user confirmation after live testing.
#
# Generated: 2026-07-04 (Saturday)
# Machine: elijah-MS-7B86 (Elijah - gaming PC)
# Verified: User confirmed controller buttons working

## 1. ACTIVE ANTIMICROX PROFILE
Path: /home/elijah/ai-controller/profiles/dont delete .gamecontroller.amgp
Loaded by: /home/elijah/ai-controller/scripts/controller-profile-switcher.sh
Service: antimicrox-autoload.service (systemd user service)
Current mode: Desktop (general use, browser, media, dictation)

### Key Mappings (from active profile XML):
- RT trigger (index 6, positive half): Qt code 0x100003c → F13 (push-to-talk)
- LT trigger (index 5, positive half): Qt code 0x1000021 → Control
- View button (index 5): executes /home/elijah/scripts/toggle-slide-keyboard.sh
- LS click (stickbutton index 2, stick 1): code 0x1000012 (Up arrow - desktop nav)
- RS click (stickbutton index 1, stick 2): code 4 (Mouse button 4)
- A button (index 1): Mouse button 1 (left click)
- B button (index 2): code 0x1000003 (Escape)
- X button (index 3): code 0x1000007 (Delete)
- Y button (index 4): code 0x1000022 (Paste script)
- LB button (index 6): code 0x1000022 + 0x1000001 (Shift + Ctrl+A)
- RB button (index 11): Mouse button 3 (right click)
- Guide button (index 8): code 0x1000000 (Space - unmapped, intentional)
- Menu button (index 7): code 0x1000001 (Ctrl)
- D-pad: arrow keys (0x1000012-15)

### Profile Structure (246 lines, 8.7 KB):
- SDL name: Xbox Series Controller
- Unique ID: 060000005e040000120b00000b05000011182834
- D-pad virtual axis associations configured
- Two control sticks with dead zones (6500) and max zones (28000)
- Triggers use positivehalf throttle mode

## 2. X11 KEYMAP PERSISTENCE
File: /home/elijah/.xmodmaprc
Content:
  keycode 191 = F13
  keycode 192 = F14

Autostart: /home/elijah/.config/autostart/load-xmodmap.desktop
  - Runs xmodmap /home/elijah/.xmodmaprc on login
  - Hidden=false, NoDisplay=false, X-GNOME-Autostart-enabled=true

## 3. PTT LISTENER (ptt_pynput.py)
Path: /home/elijah/ai-controller/scripts/ptt_pynput.py
Event source:
  - evdev fallback: /dev/input/event24 (AntiMicroX Keyboard Emulation)
  - Primary: checks EV_KEY code 0xb7 (KEY_F13)
  - EVIOCGRAB enabled: blocks F13 from reaching X11/Chrome

Dictation behavior:
  - RT press → opens mic, transcribes via Groq Whisper
  - Auto-mutes TTS audio before recording
  - Types transcript with emojis (BUBBLY, CASUAL, PRO modes)
  - Auto-space before typing restored

## 4. SERVICES (systemd user)
active antimicrox-autoload.service
  - Watches focused window + controller presence
  - Loads profile on controller plug-in
  - Kills rogue instances on re-enumeration

active ptt-pynput.service
  - Listens for F13 key events
  - Manages STT recording session

active voice-bridge.service
  - Runs on port 8002
  - Groq Whisper STT backend

## 5. CONFIGURATION FILES
~/.controller_current_profile → "Desktop"
~/.config/systemd/user/antimicrox-autoload.service
~/.config/systemd/user/ptt-pynput.service
~/.config/systemd/user/voice-bridge.service

## 6. GITHUB REPO
Private: https://github.com/ebey317/ai-controller-profile
Profile location: profiles/dont delete .gamecontroller.amgp
Last commit: pending sync (this snapshot)

## 7. VERIFIED WORKING BEHAVIOR
✅ RT trigger records → transcribes → types with emojis
✅ F13 blocked by EVIOCGRAB (no Chrome find dialog)
✅ LT = Control (unchanged)
✅ View button toggles sliding keyboard
✅ TTS auto-mutes on PTT press
✅ Auto-space before dictation working
✅ Profile loads on login via systemd service
✅ Rogue instance killer prevents duplicate AntiMicroX

## 8. ANTIMICROX_SETTINGS.INI STATE
[/home/elijah/.config/antimicrox/antimicrox_settings.ini]
LastSelected=/home/elijah/ai-controller/profiles/dont delete .gamecontroller.amgp
ProfileDir=/home/elijah/ai-controller/profiles

## 9. PROFILE FILE CHECKSUM
File: /home/elijah/ai-controller/profiles/dont delete .gamecontroller.amgp
Size: 8,722 bytes (246 lines)
Last modified: Jul 4, 2026 03:30
Permissions: rw-rw-r-- (elijah:elijah)

## 10. NOTES
- Profile name intentionally has spaces: "dont delete .gamecontroller.amgp"
- This is the ONLY active profile (no mode switching)
- good_1n.gamecontroller.amgp in ~/.config/antimicrox/ is a BACKUP COPY ONLY
- controller-profile-switcher.sh MUST be updated to point to the active profile path
- Never edit profile without live testing both RT PTT and View button toggle

## 11. CONTROLLER DEVICE INFO
Device: Xbox Series Controller (Microsoft)
USB ID: 045e:0b12
SDL controller ID: 060000005e040000120b00000b05000011182834
Driver: xone-wired (v0.3-59-g3484f60)
Input node: /dev/input/js0, /dev/input/eventXX (varies)
Audio: Microsoft Controller Audio (PulseAudio source)

## 12. KNOWN ISSUES / OPEN ITEMS
- Bluetooth controller pairing: not yet tested
- Mini-PC test (Mary): pending
- STT USB autosuspend vulnerability: controller sleep can cause "Too short" errors
  (requires keepalive or autosuspend disable)

---
Snapshot generated: 2026-07-04 $(date '+%H:%M:%S %Z')
Verified by: Elijah Wilkins (@ebey317)