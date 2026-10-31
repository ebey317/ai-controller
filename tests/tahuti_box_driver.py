#!/usr/bin/env python3
"""Broadway driver for the one-box tests (not a pytest module).

Runs the six gate scenarios against scripts/tahuti_avatar.py inside a
real GTK session on a Broadway display and prints one JSON object with
the observed results. Started by tests/test_tahuti_avatar_box.py with
GDK_BACKEND=broadway and DISPLAY pointing at a private broadwayd —
never the operator's :0 desktop.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from scripts import tahuti_avatar as av


def _buttons() -> dict:
    found = {}

    def walk(widget):
        if isinstance(widget, Gtk.Button):
            found[widget.get_label()] = widget
        if isinstance(widget, Gtk.Container):
            for child in widget.get_children():
                walk(child)

    walk(av._get_box().window)
    return found


def _after(ms, fn):
    def _run():
        fn()
        return False

    GLib.timeout_add(ms, _run)


def _toplevels() -> int:
    return len(Gtk.Window.list_toplevels())


def scenario(label, plan, timeout=30):
    """plan: list of (delay_ms, callable) executed during the prompt."""
    av.TIMEOUT_SECONDS = timeout
    for delay, fn in plan:
        _after(delay, fn)
    return av.prompt("cua:run_command:foreground", f"Scenario {label}",
                     action="run_command", mode="foreground")


def main() -> int:
    results: dict = {}
    Gtk.init([])

    # Deny — and exactly one top-level window exists while deciding.
    seen = {}
    verdict = scenario("deny", [
        (400, lambda: seen.update(count=_toplevels())),
        (600, lambda: _buttons()["Deny"].clicked()),
    ])
    results["deny"] = {"verdict": verdict, "toplevels": seen.get("count")}

    # Yes -> once. Window object must be the same box as before.
    win_before = av._get_box().window
    verdict = scenario("yes", [(400, lambda: _buttons()["Yes"].clicked())])
    results["yes"] = {"verdict": verdict,
                      "same_window": av._get_box().window is win_before}

    # Always for this session -> session.
    verdict = scenario("always", [
        (400, lambda: _buttons()["Always for this session"].clicked())])
    results["always"] = {"verdict": verdict}

    # Edit stays in the box: same window, stack flips to "edit", no new
    # toplevel; Back returns to "query"; then Deny decides.
    edit_seen = {}

    def _edit_probe():
        edit_seen["stack"] = av._get_box()._stack.get_visible_child_name()
        edit_seen["toplevels"] = _toplevels()

    def _back_probe():
        edit_seen["stack_after_back"] = \
            av._get_box()._stack.get_visible_child_name()

    verdict = scenario("edit", [
        (400, lambda: _buttons()["Edit"].clicked()),
        (800, _edit_probe),
        (1000, lambda: _buttons()["Back to request"].clicked()),
        (1400, _back_probe),
        (1600, lambda: _buttons()["Deny"].clicked()),
    ])
    results["edit"] = {"verdict": verdict, **edit_seen}

    # Close (delete-event) denies and the box survives to serve again.
    def _close():
        from gi.repository import Gdk
        av._get_box().window.emit("delete-event", Gdk.Event.new(Gdk.EventType.DELETE))

    verdict = scenario("close", [(400, _close)])
    results["close"] = {"verdict": verdict,
                        "window_alive": av._get_box().window is win_before}

    # Timeout denies on its own.
    start = time.monotonic()
    verdict = scenario("timeout", [], timeout=2)
    results["timeout"] = {"verdict": verdict,
                          "elapsed": round(time.monotonic() - start, 2)}

    av._reset_box()
    print(json.dumps(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
