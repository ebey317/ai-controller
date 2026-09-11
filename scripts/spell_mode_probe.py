#!/usr/bin/env python3
"""Headless, no-GTK, no-uinput probe: logs raw D-pad and right-stick events
from the physical controller so the actual firing pattern can be observed
live, with zero risk to any real dialog (nothing is emitted anywhere)."""
import sys
import evdev
from evdev import ecodes as e

dev = None
for path in evdev.list_devices():
    d = evdev.InputDevice(path)
    if d.name == "Microsoft Xbox Controller":
        dev = d
        break
if dev is None:
    print("CONTROLLER_NOT_FOUND", flush=True)
    sys.exit(1)

print("PROBE_READY -- move D-pad / push right stick left-right", flush=True)

PUSH_THRESHOLD = 15000
RELEASE_THRESHOLD = 5000
pressed_x = False
pressed_y = False
rx_armed = True

for event in dev.read_loop():
    if event.type == e.EV_ABS and event.code == e.ABS_HAT0X:
        if event.value != 0 and not pressed_x:
            print(f"DPAD_X {event.value}", flush=True)
            pressed_x = True
        elif event.value == 0:
            pressed_x = False
    elif event.type == e.EV_ABS and event.code == e.ABS_HAT0Y:
        if event.value != 0 and not pressed_y:
            print(f"DPAD_Y {event.value}", flush=True)
            pressed_y = True
        elif event.value == 0:
            pressed_y = False
    elif event.type == e.EV_ABS and event.code == e.ABS_RX:
        if abs(event.value) > PUSH_THRESHOLD and rx_armed:
            print(f"RSTICK_CONFIRM raw={event.value}", flush=True)
            rx_armed = False
        elif abs(event.value) < RELEASE_THRESHOLD:
            if not rx_armed:
                print("RSTICK_REARMED", flush=True)
            rx_armed = True
