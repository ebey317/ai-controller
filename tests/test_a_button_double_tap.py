"""Tests for a_button_double_tap_enter.py — device handling and tap logic.

The crash-loop incident of 2026-11-04..09 (26,454 systemd restarts because
the script exited 1 whenever the Xbox controller wasn't enumerated yet)
is guarded here by the "missing device must poll, not exit" tests.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "a_button_double_tap_enter.py"


def _load_module(monkeypatch):
    """Import the script with a fake evdev so tests never touch real hardware."""
    fake_evdev = types.ModuleType("evdev")

    class EvdevError(Exception):
        pass

    fake_evdev.ecodes = types.SimpleNamespace(EV_KEY=1, BTN_SOUTH=304)
    fake_evdev.EvdevError = EvdevError
    fake_evdev.list_devices = lambda: []
    fake_evdev.InputDevice = lambda path: (_ for _ in ()).throw(
        AssertionError("tests must not open real input devices")
    )
    monkeypatch.setitem(sys.modules, "evdev", fake_evdev)

    spec = importlib.util.spec_from_file_location(
        "a_button_double_tap_enter_under_test", SCRIPT
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_find_device_returns_none_when_controller_absent(monkeypatch):
    mod = _load_module(monkeypatch)
    assert mod.find_device() is None


def test_find_device_closes_non_matching_devices(monkeypatch):
    """A forever poll loop must not leak one fd per scanned device per scan."""
    mod = _load_module(monkeypatch)
    closed = []

    class FakeDev:
        def __init__(self, name):
            self.name = name

        def close(self):
            closed.append(self.name)

    devices = [FakeDev("Keyboard"), FakeDev("Microsoft Xbox Controller")]
    monkeypatch.setattr(mod.evdev, "list_devices", lambda: ["/dev/input/event0", "/dev/input/event1"])
    monkeypatch.setattr(mod.evdev, "InputDevice", lambda path: devices[int(path[-1])])

    found = mod.find_device()

    assert found is devices[1]
    assert closed == ["Keyboard"]  # non-match closed; match left open


def test_main_polls_instead_of_exiting_when_device_absent(monkeypatch):
    """Regression: absent controller used to raise SystemExit -> crash loop."""
    mod = _load_module(monkeypatch)
    monkeypatch.setattr(mod, "find_device", lambda: None)

    class WakeUp(Exception):
        pass

    def fake_sleep(_seconds):
        raise WakeUp  # escape the (normally infinite) poll loop after one cycle

    monkeypatch.setattr(mod.time, "sleep", fake_sleep)

    with pytest.raises(WakeUp):
        mod.main()  # reaching the sleep = it polled instead of exiting


def test_double_tap_fires_enter(monkeypatch):
    mod = _load_module(monkeypatch)
    fired = []
    monkeypatch.setattr(mod, "fire_enter", lambda: fired.append(1))

    # press(0.0) release(0.1) press(0.3) release(0.4): two quick taps,
    # gap between releases 0.3s <= DOUBLE_TAP_GAP.
    times = iter([0.0, 0.1, 0.3, 0.4])
    monkeypatch.setattr(mod.time, "monotonic", lambda: next(times))

    class FakeDev:
        name = "Microsoft Xbox Controller"

        def read_loop(self):
            for value in (1, 0, 1, 0):
                yield types.SimpleNamespace(type=1, code=304, value=value)
            raise KeyboardInterrupt  # our way to stop main() from the test

        def close(self):
            pass

    monkeypatch.setattr(mod, "find_device", lambda: FakeDev())

    with pytest.raises(KeyboardInterrupt):
        mod.main()

    assert fired == [1]


def test_long_press_is_a_drag_and_never_fires(monkeypatch):
    mod = _load_module(monkeypatch)
    fired = []
    monkeypatch.setattr(mod, "fire_enter", lambda: fired.append(1))

    class FakeDev:
        name = "Microsoft Xbox Controller"

        def close(self):
            pass

    # Stateful monotonic: press at t=0.0, release at t=0.9 (a long hold).
    times = iter([0.0, 0.9])
    monkeypatch.setattr(mod.time, "monotonic", lambda: next(times))

    def read_loop(self):
        yield types.SimpleNamespace(type=1, code=304, value=1)  # press t=0.0
        yield types.SimpleNamespace(type=1, code=304, value=0)  # release t=0.9
        raise KeyboardInterrupt

    FakeDev.read_loop = read_loop
    monkeypatch.setattr(mod, "find_device", lambda: FakeDev())

    with pytest.raises(KeyboardInterrupt):
        mod.main()

    assert fired == []  # hold = drag, not a double-tap


def test_unplug_midrun_reconnects_instead_of_crashing(monkeypatch):
    mod = _load_module(monkeypatch)
    closed = []

    class FakeDev:
        name = "Microsoft Xbox Controller"

        def read_loop(self):
            yield types.SimpleNamespace(type=1, code=304, value=1)  # press
            raise OSError("evdev node vanished (controller unplugged)")

        def close(self):
            closed.append("closed")

    results = iter([FakeDev(), None])

    class WakeUp(Exception):
        pass

    monkeypatch.setattr(mod, "find_device", lambda: next(results))
    monkeypatch.setattr(mod.time, "monotonic", lambda: 0.0)

    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) > 1:
            raise WakeUp  # second cycle reached => it re-scanned, not died

    monkeypatch.setattr(mod.time, "sleep", fake_sleep)

    with pytest.raises(WakeUp):
        mod.main()

    assert closed == ["closed"]  # old device was closed before re-scanning
