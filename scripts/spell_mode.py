#!/usr/bin/env python3
"""spell_mode.py -- controller-driven character entry for grab-holding secure
dialogs (gnome-keyring's "Unlock Login Keyring", polkit's auth prompt).

Deliberately separate from slide_keyboard.py: that keyboard is a GTK
mouse-click widget tree, and a mouse click (real or controller-driven)
physically cannot reach any window except the one holding an active X11
pointer grab -- confirmed live, this session, via python-xlib
(root.grab_pointer()/grab_keyboard() both return AlreadyGrabbed the entire
time such a dialog is open). Mixing that click-based architecture with a
raw-evdev/uinput input model in one file would entangle two very different
mechanisms, per the original design doc (~/.claude/plans/zesty-watching-neumann.md).

This window is DISPLAY-ONLY -- no Gtk.Button, no click handlers, no
dependency on receiving real input events at all, so the pointer grab can
never affect it. It only repaints in response to state changes pushed in
from the evdev listener thread via GLib.idle_add.

Confirm is the RIGHT STICK, left/right (ABS_RX) -- not any face button, not
a trigger. Elijah confirmed live (2026-09-04) that LT (Ctrl) double-fires
from analog jitter when held, and that LS-click is actually mapped to
Escape in his real AntiMicroX profile (would cancel the dialog on every
confirm). Right-stick left/right is the one input he confirmed does nothing
in his current config. It's still analog, so the same jitter risk as LT
applies in principle -- guarded here with a wide threshold (15000/32767) and
a separate, much smaller release threshold (5000) before the next push can
fire, so a single deliberate push-and-return can't double-count the way a
resting analog value near a single threshold can.

Character emission goes through a genuine uinput virtual keyboard device
(NOT xdotool/XTest) -- this is the mechanism proven live in this session:
with the target dialog's pointer+keyboard grab both confirmed active via
python-xlib, a single uinput-emitted keypress landed correctly in its
password field. A uinput device is indistinguishable from real hardware at
the X11/kernel level (confirmed via `xinput list`: AntiMicroX's own output
devices show up the same way), so it isn't subject to any of the
grab-related routing questions xdotool/focus_guard's EWMH-active-window
checks have to work around.
"""
import logging
import sys
import time

import evdev
from evdev import ecodes as e, UInput
import cairo
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Kept in sync by hand with slide_keyboard.py's ROWS_LOWER/ROWS_UPPER -- see
# module docstring for why this isn't a shared import.
ROWS_LOWER = [
    ["`", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "="],
    ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p", "[", "]", "\\"],
    ["a", "s", "d", "f", "g", "h", "j", "k", "l", ";", "'"],
    ["z", "x", "c", "v", "b", "n", "m", ",", ".", "/"],
    ["bksp", "space", "enter"],
]
ROWS_UPPER = [
    ["~", "!", "@", "#", "$", "%", "^", "&", "*", "(", ")", "_", "+"],
    ["Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P", "{", "}", "|"],
    ["A", "S", "D", "F", "G", "H", "J", "K", "L", ":", '"'],
    ["Z", "X", "C", "V", "B", "N", "M", "<", ">", "?"],
    ["bksp", "space", "enter"],
]
LABELS = {"bksp": "⌫", "space": "␣", "enter": "⏎"}

# char -> (evdev KEY_* code, needs_shift). Covers every character cell in
# ROWS_LOWER/ROWS_UPPER above. Standard US QWERTY layout.
_CHAR_KEYS = {}
for _c in "abcdefghijklmnopqrstuvwxyz":
    _CHAR_KEYS[_c] = (getattr(e, f"KEY_{_c.upper()}"), False)
    _CHAR_KEYS[_c.upper()] = (getattr(e, f"KEY_{_c.upper()}"), True)
_digit_shift = {"1": "!", "2": "@", "3": "#", "4": "$", "5": "%",
                 "6": "^", "7": "&", "8": "*", "9": "(", "0": ")"}
for _d, _sym in _digit_shift.items():
    key = getattr(e, f"KEY_{_d}")
    _CHAR_KEYS[_d] = (key, False)
    _CHAR_KEYS[_sym] = (key, True)
_symbol_pairs = [
    ("`", "~", "KEY_GRAVE"), ("-", "_", "KEY_MINUS"), ("=", "+", "KEY_EQUAL"),
    ("[", "{", "KEY_LEFTBRACE"), ("]", "}", "KEY_RIGHTBRACE"),
    ("\\", "|", "KEY_BACKSLASH"), (";", ":", "KEY_SEMICOLON"),
    ("'", '"', "KEY_APOSTROPHE"), (",", "<", "KEY_COMMA"),
    (".", ">", "KEY_DOT"), ("/", "?", "KEY_SLASH"),
]
for _lo, _hi, _keyname in _symbol_pairs:
    key = getattr(e, _keyname)
    _CHAR_KEYS[_lo] = (key, False)
    _CHAR_KEYS[_hi] = (key, True)

_CONTROL_KEYS = {"bksp": e.KEY_BACKSPACE, "space": e.KEY_SPACE, "enter": e.KEY_ENTER}

# All KEY_* codes this device will ever need to declare as capabilities.
_ALL_KEYS = sorted({code for code, _ in _CHAR_KEYS.values()} | set(_CONTROL_KEYS.values()) | {e.KEY_LEFTSHIFT})

CSS = b"""
#panel { background-color: rgba(13,13,18,0.94); border: 2px solid #FF6A00; border-radius: 10px; }
label { color: #d8d8dc; font-family: monospace; font-size: 18px; padding: 4px 8px; }
label.cursor { background-color: #FF6A00; color: #0d0d12; font-weight: bold; border-radius: 4px; }
label.control { color: #FF6A00; }
"""


class SpellMode(Gtk.Window):
    def __init__(self, ui: UInput):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.ui = ui
        self.shift_on = False
        self.cursor = (0, 0)
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)
        css = Gtk.CssProvider()
        css.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            screen, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        panel = Gtk.EventBox()
        panel.set_name("panel")
        panel.get_style_context().add_class("panel")
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        outer.set_margin_start(10)
        outer.set_margin_end(10)
        outer.set_margin_top(8)
        outer.set_margin_bottom(8)
        panel.add(outer)
        self.add(panel)

        title = Gtk.Label(label="SPELL MODE -- right stick left/right = confirm")
        title.set_markup('<span foreground="#FF6A00" weight="bold">SPELL MODE -- right stick left/right = confirm</span>')
        outer.pack_start(title, False, False, 0)

        self.grid = Gtk.Grid(column_spacing=6, row_spacing=4)
        outer.pack_start(self.grid, True, True, 0)
        self._build_keys()

        screen_w = screen.get_width()
        screen_h = screen.get_height()
        self.show_all()
        # Small and pinned to the bottom-right corner -- deliberately never
        # centered, so it can't sit on top of the dialog it's typing into.
        self.resize(420, 200)
        self.move(screen_w - 440, screen_h - 240)

    def _labels(self):
        return ROWS_UPPER if self.shift_on else ROWS_LOWER

    def _build_keys(self):
        for child in self.grid.get_children():
            self.grid.remove(child)
        self.cells = {}
        rows = self._labels()
        for r, row in enumerate(rows):
            for c, key in enumerate(row):
                text = LABELS.get(key, key)
                lbl = Gtk.Label(label=text)
                if key in _CONTROL_KEYS:
                    lbl.get_style_context().add_class("control")
                self.grid.attach(lbl, c, r, 1, 1)
                self.cells[(r, c)] = lbl
        self.grid.show_all()
        self._refresh_highlight()

    def _refresh_highlight(self):
        for pos, lbl in self.cells.items():
            ctx = lbl.get_style_context()
            if pos == self.cursor:
                ctx.add_class("cursor")
            else:
                ctx.remove_class("cursor")

    def move_cursor(self, dr, dc):
        rows = self._labels()
        r, c = self.cursor
        r = max(0, min(len(rows) - 1, r + dr))
        c = max(0, min(len(rows[r]) - 1, c + dc))
        self.cursor = (r, c)
        self._refresh_highlight()

    def confirm(self):
        rows = self._labels()
        r, c = self.cursor
        key = rows[r][c]
        self._emit(key)

    def _emit(self, key):
        if key in _CONTROL_KEYS:
            code = _CONTROL_KEYS[key]
            self.ui.write(e.EV_KEY, code, 1)
            self.ui.write(e.EV_SYN, e.SYN_REPORT, 0)
            self.ui.write(e.EV_KEY, code, 0)
            self.ui.write(e.EV_SYN, e.SYN_REPORT, 0)
            log.info(f"emitted control key: {key}")
            return
        code, needs_shift = _CHAR_KEYS[key]
        if needs_shift:
            self.ui.write(e.EV_KEY, e.KEY_LEFTSHIFT, 1)
            self.ui.write(e.EV_SYN, e.SYN_REPORT, 0)
        self.ui.write(e.EV_KEY, code, 1)
        self.ui.write(e.EV_SYN, e.SYN_REPORT, 0)
        self.ui.write(e.EV_KEY, code, 0)
        self.ui.write(e.EV_SYN, e.SYN_REPORT, 0)
        if needs_shift:
            self.ui.write(e.EV_KEY, e.KEY_LEFTSHIFT, 0)
            self.ui.write(e.EV_SYN, e.SYN_REPORT, 0)
        log.info("emitted one character key (value not logged)")


def _find_controller():
    for path in evdev.list_devices():
        d = evdev.InputDevice(path)
        if d.name == "Microsoft Xbox Controller":
            return d
    return None


def nav_loop(win: SpellMode):
    dev = _find_controller()
    if dev is None:
        log.error("'Microsoft Xbox Controller' not found -- nav loop exiting")
        return

    PUSH_THRESHOLD = 15000
    RELEASE_THRESHOLD = 5000
    pressed_x = False
    pressed_y = False
    rx_armed = True  # ready to fire; disarmed after a push until it returns to center

    for event in dev.read_loop():
        if event.type == e.EV_ABS and event.code == e.ABS_HAT0X:
            if event.value != 0 and not pressed_x:
                GLib.idle_add(win.move_cursor, 0, event.value)
                pressed_x = True
            elif event.value == 0:
                pressed_x = False
        elif event.type == e.EV_ABS and event.code == e.ABS_HAT0Y:
            if event.value != 0 and not pressed_y:
                GLib.idle_add(win.move_cursor, event.value, 0)
                pressed_y = True
            elif event.value == 0:
                pressed_y = False
        elif event.type == e.EV_ABS and event.code == e.ABS_RX:
            if abs(event.value) > PUSH_THRESHOLD and rx_armed:
                GLib.idle_add(win.confirm)
                rx_armed = False
            elif abs(event.value) < RELEASE_THRESHOLD:
                rx_armed = True


def main():
    caps = {e.EV_KEY: _ALL_KEYS}
    ui = UInput(caps, name="spell-mode-keyboard")
    log.info("uinput device 'spell-mode-keyboard' created")

    win = SpellMode(ui)
    win.connect("destroy", lambda *_: (ui.close(), Gtk.main_quit()))

    import threading
    threading.Thread(target=nav_loop, args=(win,), daemon=True).start()

    Gtk.main()


if __name__ == "__main__":
    main()
