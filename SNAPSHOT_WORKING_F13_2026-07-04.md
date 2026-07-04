# Voice/Controller Pipeline State Snapshot — 2026-07-04
# Working config — DO NOT CHANGE without testing first.

## AntiMicrox profile
/home/elijah/ai-controller/profiles/dont delete .gamecontroller.amgp
  - RT (trigger index 6, positive half): Qt code 0x100003c → X11 keycode 191 → F13
  - LT (trigger index 7, positive half): Qt code 0x1000021 → Control

## X keymap persistence
~/.xmodmaprc
  keycode 191 = F13
  keycode 192 = F14
~/.config/autostart/load-xmodmap.desktop
  runs xmodmap /home/elijah/.xmodmaprc on login

## PTT listener
~/ai-controller/scripts/ptt_pynput.py
  - evdev fallback listens on /dev/input/event24 (AntiMicroX Keyboard Emulation)
  - checks EV_KEY code 0xb7 (KEY_F13)
  - uses EVIOCGRAB to prevent F13 from reaching X11/Chrome

## Services
- antimicrox-autoload.service — loads good_1n.gamecontroller.amgp
- ptt-pynput.service — restarted after profile/PTT code changes
- voice-bridge.service — runs on :8002, Groq Whisper STT

## Verified behavior
- RT records, transcribes, types text with emojis
- F13 does NOT open Chrome find/search because EVIOCGRAB blocks it
- LT = Control (not changed)
