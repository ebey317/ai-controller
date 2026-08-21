#!/usr/bin/env python3
"""A-button double-tap -> Enter.

Reads the physical Xbox controller directly via evdev (read-only, no grab),
alongside antimicrox's own click/drag mapping on the same button -- this
does NOT replace or interfere with that. It only watches BTN_SOUTH (A) press/
release timing and, when it sees two quick taps in a row, fires a real
`xdotool key Return`. A single tap or a long press-and-hold (drag) never
triggers it, so the existing click/drag behavior on A is untouched.
"""
import subprocess
import time

import evdev
from evdev import ecodes

DEVICE_NAME = "Microsoft Xbox Controller"
BUTTON_CODE = ecodes.BTN_SOUTH  # A button (304)

QUICK_TAP_MAX = 0.30   # press-to-release longer than this = hold/drag, not a tap
DOUBLE_TAP_GAP = 0.35  # max gap between the two taps' releases/press


def find_device():
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        if dev.name == DEVICE_NAME:
            return dev
    return None


def fire_enter():
    subprocess.run(["xdotool", "key", "Return"], check=False)


def main():
    dev = find_device()
    if dev is None:
        raise SystemExit(f"controller '{DEVICE_NAME}' not found")

    press_time = None
    last_quick_tap_time = None

    for event in dev.read_loop():
        if event.type != ecodes.EV_KEY or event.code != BUTTON_CODE:
            continue

        now = time.monotonic()

        if event.value == 1:  # press
            press_time = now
        elif event.value == 0:  # release
            if press_time is None:
                continue
            duration = now - press_time
            press_time = None

            if duration > QUICK_TAP_MAX:
                # long hold = drag, doesn't count toward a double-tap
                last_quick_tap_time = None
                continue

            if last_quick_tap_time is not None and (now - last_quick_tap_time) <= DOUBLE_TAP_GAP:
                fire_enter()
                last_quick_tap_time = None
            else:
                last_quick_tap_time = now


if __name__ == "__main__":
    main()
