# AI Controller Working Snapshot — 2026-07-04
# Machine: elijah-MS-7B86 (Elijah)
# Verified: controller + headset + STT + TTS + services all working

## Driver stack
- xone-wired (gamepad + audio path)
- xone_gip
- xone_gip_headset
- xone_gip_gamepad
- xpad NOT loaded (would break headset audio)

## Controller
- Path: /home/elijah/ai-controller/profiles/dont delete .gamecontroller.amgp
- AntiMicroX command: /usr/bin/antimicrox --profile ".../dont delete .gamecontroller.amgp" --tray --eventgen uinput
- xinput shows antimicrox Keyboard/Mouse/Abs Mouse emulation
- RT -> F13 -> PTT -> STT
- LT -> Control

## Audio devices
- Sink: alsa_output.usb-Microsoft_Controller_3039373130383038333134313433-00.stereo-fallback
- Source: alsa_input.usb-Microsoft_Controller_3039373130383038333134313433-00.mono-fallback
- Card: alsa_card.usb-Microsoft_Controller_3039373130383038333134313433-00

## Services active
- antimicrox-autoload.service
- voice-bridge.service
- ptt-pynput.service
- controller-legend.service
- ai-slide-keyboard.service

## Key survival fixes
1. /etc/modprobe.d/blacklist-xone-wired.conf.disabled — original xone blacklist moved aside
2. /etc/modules-load.d/xbox-controller.conf — removed; xone auto-loads from dkms
3. /etc/udev/rules.d/99-xbox-controller.rules — starts services on controller plug-in
4. ai-controller-launcher.py now has "Fix Controller + Start Services" button
5. ai-controller-healthcheck.sh runs every minute to reload xone if headset card missing
6. GitHub pushed: https://github.com/ebey317/ai-controller

## Recovery if this breaks again
1. Unplug controller, wait 5s, replug (fixes GIP firmware state)
2. Click "Fix Controller + Start Services" in launcher
3. If still dead, run `bash /home/elijah/ai-controller/scripts/reset_xbox_headset.sh`

## Commit
19398e3 Working snapshot 2026-07-04_09:37:55 — controller + headset + services verified
0f7a6f9 Remove .venv from repo
