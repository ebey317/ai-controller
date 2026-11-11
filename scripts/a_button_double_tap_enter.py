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

QUICK_TAP_MAX = 0.30  # press-to-release longer than this = hold/drag, not a tap
DOUBLE_TAP_GAP = 0.35  # max gap between the two taps' releases/press

DEVICE_POLL_SECONDS = 2.0  # re-scan cadence while the controller is absent


def find_device():
    """Return the controller device, or None if it isn't plugged in.

    Devices that don't match are closed again so a long-running poll loop
    doesn't leak a file descriptor per scanned device per scan.
    """
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            # Device vanished between list_devices() and open() -- skip it.
            continue
        if dev.name == DEVICE_NAME:
            return dev
        dev.close()
    return None


def fire_enter():
    subprocess.run(["xdotool", "key", "Return"], check=False)


def main():
    # Reconnect loop: the controller may not be present at boot (the xone
    # driver can enumerate it later), and it may be unplugged at any time.
    # Neither case should crash-loop the service (2026-11-04..09: 26k restarts
    # when this used to exit 1 on a missing controller). We poll for the
    # device instead, and re-scan if read_loop dies mid-stream.
    press_time = None
    last_quick_tap_time = None

    while True:
        dev = find_device()
        if dev is None:
            time.sleep(DEVICE_POLL_SECONDS)
            continue

        # Fresh device: stale tap state from the old one would otherwise
        # pair a pre-unplug release with a post-replug press.
        press_time = None
        last_quick_tap_time = None

        try:
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

                    if (
                        last_quick_tap_time is not None
                        and (now - last_quick_tap_time) <= DOUBLE_TAP_GAP
                    ):
                        fire_enter()
                        last_quick_tap_time = None
                    else:
                        last_quick_tap_time = now
        except (OSError, evdev.EvdevError):
            # Controller unplugged or the evdev node went away. Fall through
            # to the re-scan; exit codes (and systemd restarts) stay reserved
            # for real failures.
            pass

        try:
            dev.close()
        except OSError:
            pass
        time.sleep(DEVICE_POLL_SECONDS)


if __name__ == "__main__":
    main()
