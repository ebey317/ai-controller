#!/usr/bin/env python3
"""
slide_keyboard.py — Floating, centered on-screen keyboard.

Replaces Onboard (its accessibility "scanner" mode never moved off the
backtick key — confirmed broken independent of the controller, 2026-06-16).

No scanner, no highlight-and-select. Every key is a real GTK button:
click it (controller A + L-stick-as-mouse, or a real mouse) and it sends
that keystroke straight to whatever window currently has focus, via
xdotool. The keyboard window itself never takes focus
(set_accept_focus(False)), so the target window stays focused throughout.

Trigger: Guide button (AntiMicroX sends F14) pops it up centered on screen,
styled to match the controller-legend HUD (same dark/orange theme, rounded
panel) so it reads as part of the same floating-overlay family instead of
a separate full-width dock. Press F14 again to pop it back down.
"""
import json
import logging
import os
import signal
import subprocess
import sys
import warnings

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger(__name__)

# GTK3 deprecation noise is not useful in production.
warnings.filterwarnings("ignore", category=DeprecationWarning)

import cairo
import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib, Gtk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import focus_guard
import voice_toggle
from ai_controller_paths import config_dir, ensure_config_dir

# Shared with ptt_pynput.py: PRO = plain text, BUBBLY = cursive + emoji
ensure_config_dir()
PTT_MODE_FILE = os.path.join(config_dir(), "ptt_mode")
INPUT_TARGET_FILE = os.path.join(config_dir(), "ai_controller_input_target")
TYPING_STATE_FILE = "/tmp/ptt_typing_state"

# Persistent snippet pins — separate from the OS clipboard (which a single
# new copy silently overwrites). These live in the empty strip to the right
# of the arrow/space row and survive until explicitly unpinned.
PINS_FILE = os.path.join(config_dir(), "pinned_snippets.json")
PIN_SLOTS = 7  # columns 7-13 of row 4; col 14 is reserved for the + button
DEFAULT_PINS = [
    {"label": "hermes", "text": "hermes --tui"},
    {"label": "claude", "text": "claude"},
    {"label": "desktop", "text": "hermes desktop"},
    {"label": "qwen", "text": "qwen"},
]

ROWS_LOWER = [
    ["`", "esc", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "=", "bksp"],
    ["tab", "q", "w", "e", "r", "t", "y", "u", "i", "o", "p", "[", "]", "\\"],
    ["a", "s", "d", "f", "g", "h", "j", "k", "l", ";", "'", "enter"],
    ["shift", "z", "x", "c", "v", "b", "n", "m", ",", ".", "/", "shift"],
    ["left", "down", "up", "right", "space", "⇧tab"],
]
ROWS_UPPER = [
    ["~", "esc", "!", "@", "#", "$", "%", "^", "&", "*", "(", ")", "_", "+", "bksp"],
    ["tab", "Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P", "{", "}", "|"],
    ["A", "S", "D", "F", "G", "H", "J", "K", "L", ":", '"', "enter"],
    ["shift", "Z", "X", "C", "V", "B", "N", "M", "<", ">", "?", "shift"],
    ["left", "down", "up", "right", "space", "⇧tab"],
]

# keys that send a named X11 key (xdotool key ...) instead of a literal char
SPECIAL = {
    "esc": "Escape", "bksp": "BackSpace", "tab": "Tab", "enter": "Return",
    "space": "space", "left": "Left", "right": "Right", "up": "Up", "down": "Down",
    "⇧tab": "shift+Tab",
}
LABELS = {
    "esc": "Esc", "bksp": "⌫", "tab": "Tab ⇥", "enter": "Enter ↵",
    "space": "Space", "left": "←", "right": "→", "up": "↑", "down": "↓",
    "shift": "⇧", "⇧tab": "⇧Tab\n(cycle)",
}

# Same dark/orange family as controller-legend.py's HUD, so the two read as
# one floating-overlay system instead of unrelated widgets.
def _modifier_state():
    """Return (ctrl, alt, shift) from live X11 keyboard state.

    Query X11 directly via python-xlib rather than GTK. The keyboard window
    never takes focus (set_accept_focus(False)), so GTK/Gdk's modifier
    state can lag or miss synthetic modifier events from the controller.
    X11's global keymap state matches real-keyboard behaviour so
    Ctrl/Shift/Alt held by the controller are detected.
    """
    try:
        import Xlib.display
        disp = Xlib.display.Display()
        keymap = disp.query_keymap()
        # Standard X11 modifier keycodes:
        #   Control_L = 37, Control_R = 109
        #   Shift_L = 50, Shift_R = 62
        #   Alt_L (Mod1) = 64, Alt_R = 108
        ctrl = bool(keymap[37 >> 3] & (1 << (37 & 7))) or bool(keymap[109 >> 3] & (1 << (109 & 7)))
        shift = bool(keymap[50 >> 3] & (1 << (50 & 7))) or bool(keymap[62 >> 3] & (1 << (62 & 7)))
        alt = bool(keymap[64 >> 3] & (1 << (64 & 7))) or bool(keymap[108 >> 3] & (1 << (108 & 7)))
        disp.close()
        return ctrl, alt, shift
    except Exception:
        return False, False, False


HUD_ORANGE = "#FF6A00"

CSS = b"""
window { background-color: transparent; }
#panel {
    background-color: rgba(13,13,18,0.94);
    border: 2px solid #FF6A00;
    border-radius: 16px;
}
#drag-handle {
    background-color: rgba(26,26,32,0.85);
    border: 1px solid #3a3a44;
    border-radius: 8px;
}
.handle-label {
    color: #8a8a92;
    font-family: monospace;
    font-size: 11px;
}
button {
    background-image: none;
    background-color: #23232b;
    color: #e8e8e8;
    border: 1px solid #3a3a44;
    border-radius: 6px;
    font-family: monospace;
    font-size: 13px;
    min-width: 34px;
    min-height: 34px;
    padding: 4px;
}
button:hover { background-color: #2f2f3a; }
button.special { background-color: #1a2226; color: #FF6A00; border-color: #4a3318; }
button.mode { background-color: #2a1a0a; color: #FF6A00; border-color: #FF6A00; font-weight: bold; padding: 2px 10px; }
button.mode-active { background-color: #FF6A00; color: #0d0d12; border-color: #FF6A00; font-weight: bold; padding: 2px 10px; }
.shelf-title { color: #FF6A00; font-weight: bold; font-size: 11px; margin-bottom: 4px; }
button.pin { background-color: #0f2a24; color: #3ddc97; border-color: #1f5c4a; font-size: 11px; }
button.pin:hover { background-color: #163a30; }
button.pin-add { background-color: #23232b; color: #6a6a72; border-color: #3a3a44; }
button.pin-add:hover { background-color: #2f2f3a; color: #3ddc97; }
"""


def send(key, ctrl=False, alt=False, shift=False, target_win=None):
    """Send `key` to `target_win`, verified focused first.

    The keyboard window never takes focus (see class docstring), so every
    keystroke used to fire at whatever xdotool considered "active" with no
    check at all. If anything nudged focus elsewhere between opening the
    keyboard and this click -- confirmed live: a typed sequence once landed
    in an unrelated terminal instead of the intended dialog -- it went
    wherever focus actually was, silently. Now it either lands where the
    user is looking at, or it's skipped and logged -- never a silent
    mistype into the wrong window.
    """
    if key == "shift":
        return
    try:
        if ctrl or alt or shift:
            prefix = ("ctrl+" if ctrl else "") + ("alt+" if alt else "") + ("shift+" if shift else "")
            key_spec = prefix + (SPECIAL[key] if key in SPECIAL else key.lower())
            focus_guard.guarded_key(target_win, key_spec)
        elif key in SPECIAL:
            focus_guard.guarded_key(target_win, SPECIAL[key])
        else:
            focus_guard.guarded_type(target_win, key)
    except focus_guard.FocusLostError as exc:
        log.warning(f"{exc} — key '{key}' not sent")


class SlideKeyboard(Gtk.Window):
    # Width fits the key grid and mode bar only (no voice-profile shelf).
    WIDTH = 860
    HEIGHT = 300
    POP_OFFSET = 36  # px it rises from on pop-in, for the "pop" feel

    def __init__(self):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)  # NEVER steal focus from the target window
        self.set_app_paintable(True)
        # Drag state for grab-anywhere moves on this override-redirect popup.
        self._drag_active = False
        self._drag_offset_x = 0
        self._drag_offset_y = 0

        # RGBA visual so the window background can be fully transparent —
        # without this the rounded #panel corners would show square behind
        # them (same pattern as controller-legend.py / hud_keyboard_gui.py).
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)

        css = Gtk.CssProvider()
        css.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            screen, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.shift_on = False

        # Outer styled panel (the rounded orange-bordered box) wraps the key
        # grid, matching the legend HUD's visual language.
        self.panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.panel.set_name("panel")
        self.panel.set_margin_start(10)
        self.panel.set_margin_end(10)
        self.panel.set_margin_top(10)
        self.panel.set_margin_bottom(10)
        self.add(self.panel)

        # DRAG HANDLE — a thin labeled bar at the very top of the panel.
        # The keyboard is dense buttons; without a dedicated non-button area,
        # the window-level drag never fires (every click lands on a button
        # widget that swallows the event). This handle gives a visible
        # grabbable target anywhere along the top of the keyboard.
        self.drag_handle = Gtk.EventBox()
        self.drag_handle.set_name("drag-handle")
        self.drag_handle.set_above_child(False)
        self.drag_handle.connect("realize", self._on_drag_handle_realize)
        handle_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        handle_row.set_margin_start(8)
        handle_row.set_margin_end(8)
        handle_row.set_margin_top(4)
        handle_row.set_margin_bottom(4)
        handle_label = Gtk.Label(label="\u2261  drag")
        handle_label.set_xalign(0.5)
        handle_label.get_style_context().add_class("handle-label")
        handle_row.pack_start(handle_label, True, True, 0)
        self.drag_handle.add(handle_row)
        self.drag_handle.connect("button-press-event", self._on_handle_drag_begin)
        self.drag_handle.connect("motion-notify-event", self._on_handle_drag_motion)
        self.drag_handle.connect("button-release-event", self._on_handle_drag_end)
        self.panel.pack_start(self.drag_handle, False, False, 0)

        # CONTENT ROW: the existing button grid + typing indicator go here.
        self.content_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.panel.pack_start(self.content_row, True, True, 0)

        # LEFT COLUMN: mode bar across the top + key grid below it.
        self.left_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.content_row.pack_start(self.left_col, True, True, 0)

        # Mode bar: buttons left-to-right, ending at PRO.
        self.mode_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.mode_bar.set_margin_start(8)
        self.mode_bar.set_margin_end(8)
        self.mode_bar.set_margin_top(6)
        self.mode_bar.set_spacing(6)
        self.left_col.pack_start(self.mode_bar, False, False, 0)

        self._mode_buttons = []
        for mode in ("bubbly", "casual", "bold", "big", "pro"):
            labels = {
                "bubbly": "✨",
                "casual": "☕ CASUAL",
                "bold": "BOLD",
                "big": "BIG",
                "pro": "PRO",
            }
            btn = Gtk.Button(label=labels[mode])
            btn.get_style_context().add_class("mode")
            btn.connect("clicked", self._on_mode_set, mode)
            self.mode_bar.pack_start(btn, False, False, 0)
            self._mode_buttons.append((mode, btn))

        # Voice toggle: cycles between unlocked voice packs (aria ↔ joe).
        self.voice_btn = Gtk.Button(label=self._voice_label())
        self.voice_btn.get_style_context().add_class("mode")
        self.voice_btn.connect("clicked", self._on_voice_toggle)
        self.mode_bar.pack_end(self.voice_btn, False, False, 0)

        # Input target toggle: type directly vs copy to clipboard only.
        self.target_btn = Gtk.Button(label=self._target_label())
        self.target_btn.get_style_context().add_class("mode")
        self.target_btn.connect("clicked", self._on_target_toggle)
        self.mode_bar.pack_end(self.target_btn, False, False, 0)

        self._refresh_mode_buttons()

        self.grid = Gtk.Grid(column_spacing=4, row_spacing=4)
        self.grid.set_margin_start(8)
        self.grid.set_margin_end(8)
        self.grid.set_margin_top(6)
        self.grid.set_margin_bottom(8)
        self.left_col.pack_start(self.grid, True, True, 0)
        self._build_keys()

        # TYPING INDICATOR: replaces the keyboard content while Unicode modes
        # emit long text. Centered in the panel, same dark/orange theme.
        self.typing_indicator = Gtk.Label(label="")
        self.typing_indicator.set_name("typing-indicator")
        self.typing_indicator.set_markup(
            f'<span font="16" weight="bold" color="{HUD_ORANGE}">✨  Typing...</span>'
        )
        self.typing_indicator.set_margin_start(40)
        self.typing_indicator.set_margin_end(40)
        self.typing_indicator.set_margin_top(30)
        self.typing_indicator.set_margin_bottom(30)
        self.content_row.pack_start(self.typing_indicator, True, True, 0)
        self.typing_indicator.hide()

        self.sw = screen.get_width()
        self.sh = screen.get_height()
        self.center_x = (self.sw - self.WIDTH) // 2
        self.center_y = (self.sh - self.HEIGHT) // 2
        self.hidden_y = self.center_y + self.POP_OFFSET

        # Gtk.WindowType.POPUP windows are override-redirect — the window
        # manager never sizes them, so set_default_size() before realization
        # is a no-op (this is why the window showed up as a stray 10x10 box
        # at 10,10). Force the size with resize(), then show_all() once to
        # realize it, then move off-screen (opacity 0) and park centered.
        # Toggling pops it in/out via combined move + fade, no further
        # show()/hide() calls, which sidesteps any more realize-timing
        # surprises.
        self.resize(self.WIDTH, self.HEIGHT)
        self.show_all()
        self.move(self.center_x, self.hidden_y)
        self.set_opacity(0.0)
        self.visible_state = False
        self._anim_id = None
        self._focus_target_win = None  # snapshotted when the keyboard opens

        # This window sits mapped and centered on-screen at all times, even
        # in its "hidden" (opacity 0) resting state -- an invisible window is
        # still solid to the mouse unless its input shape says otherwise, so
        # every click/hover under its footprint (whatever app that happens to
        # be) silently never reached the real target. Same bug class as the
        # controller-legend HUD, just never fixed here in the first place.
        self.connect("realize", lambda *_: self._apply_input_passthrough(not self.visible_state))
        self.connect("map-event", lambda *_: self._apply_input_passthrough(not self.visible_state))
        self._apply_input_passthrough(True)

        # Poll typing state from ptt_pynput.py so the keyboard can transform
        # into a typing indicator while Unicode modes emit.
        self._typing_poll_id = GLib.timeout_add(100, self._check_typing_state)

        # Mouse events on the popup itself handle window dragging (since
        # override-redirect windows have no WM titlebar). Any drag that doesn't
        # land on a clickable widget (button, etc.) moves the whole keyboard.
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.BUTTON_MOTION_MASK)
        self.connect("button-press-event", self._on_window_button_press)
        self.connect("button-release-event", self._on_window_button_release)
        self.connect("motion-notify-event", self._on_window_motion)

    def _on_window_button_press(self, widget, event):
        # Only primary button drags the window; ignore clicks on actual buttons
        # (they have their own handlers and event propagation stops there).
        if event.button == 1 and not self._drag_active:
            # Record offset so the window doesn't snap to the top-left of click.
            self._drag_offset_x = int(event.x)
            self._drag_offset_y = int(event.y)
            self._drag_active = True
        return False

    def _on_window_button_release(self, widget, event):
        if event.button == 1:
            self._drag_active = False
        return False

    def _on_window_motion(self, widget, event):
        if not self._drag_active:
            return False
        # event.x_root/y_root are in screen coordinates.
        new_x = int(event.x_root - self._drag_offset_x)
        new_y = int(event.y_root - self._drag_offset_y)
        new_x, new_y = self._clamp_to_screen(new_x, new_y)
        self.move(new_x, new_y)
        # Update center tracking so the next pop animation stays relative to
        # wherever the user placed it.
        self.center_x = new_x
        self.center_y = new_y
        self.hidden_y = self.center_y + self.POP_OFFSET
        return False

    # ─── DRAG HANDLE ────────────────────────────────────────────────
    # The keyboard is dense buttons; the window-level handlers above only
    # fire on empty (non-button) regions, of which there are none. The
    # handle bar at the top of the panel is non-interactive chrome, so
    # button-press events reach our handler and we can drag from there.

    def _on_handle_drag_begin(self, _widget, event):
        if event.button == 1:
            pos_x, pos_y = self.get_position()
            self._drag_offset_x = int(event.x_root - pos_x)
            self._drag_offset_y = int(event.y_root - pos_y)
            self._drag_active = True
            # Don't propagate — we want the drag to start here, not fall
            # through to the window-level handler.
            return True
        return False

    def _on_handle_drag_motion(self, _widget, event):
        if not self._drag_active:
            return False
        new_x = int(event.x_root - self._drag_offset_x)
        new_y = int(event.y_root - self._drag_offset_y)
        new_x, new_y = self._clamp_to_screen(new_x, new_y)
        self.move(new_x, new_y)
        self.center_x = new_x
        self.center_y = new_y
        self.hidden_y = self.center_y + self.POP_OFFSET
        return True

    def _on_handle_drag_end(self, _widget, _event):
        self._drag_active = False
        return True

    def _on_drag_handle_realize(self, widget):
        try:
            widget.get_window().set_cursor(
                Gdk.Cursor.new_from_name(Gdk.Display.get_default(), "grab")
            )
        except Exception:
            pass

    def _clamp_to_screen(self, x: int, y: int):
        """Keep the keyboard at least 40px on-screen so it can't be lost
        off a screen edge during drag."""
        margin = 40
        if x < -self.WIDTH + margin:
            x = -self.WIDTH + margin
        if y < -self.HEIGHT + margin:
            y = -self.HEIGHT + margin
        if x > self.sw - margin:
            x = self.sw - margin
        if y > self.sh - margin:
            y = self.sh - margin
        return x, y

    def _load_ptt_mode(self) -> str:
        try:
            with open(PTT_MODE_FILE, "r", encoding="utf-8") as f:
                mode = f.read().strip().lower()
                if mode in ("pro", "bubbly", "casual", "bold", "big"):
                    return mode
        except Exception:
            pass
        return "pro"

    def _save_ptt_mode(self, mode: str) -> None:
        os.makedirs(os.path.dirname(PTT_MODE_FILE), exist_ok=True)
        with open(PTT_MODE_FILE, "w", encoding="utf-8") as f:
            f.write(mode)

    def _refresh_mode_buttons(self):
        current = self._load_ptt_mode()
        for mode, btn in self._mode_buttons:
            if mode == current:
                btn.get_style_context().add_class("mode-active")
                btn.get_style_context().remove_class("mode")
            else:
                btn.get_style_context().add_class("mode")
                btn.get_style_context().remove_class("mode-active")

    def _on_mode_set(self, _widget, mode):
        self._save_ptt_mode(mode)
        self._refresh_mode_buttons()

    def _load_input_target(self) -> str:
        try:
            with open(INPUT_TARGET_FILE, "r", encoding="utf-8") as f:
                t = f.read().strip().lower()
                if t in ("type", "clipboard"):
                    return t
        except Exception:
            pass
        return "type"

    def _save_input_target(self, target: str) -> None:
        os.makedirs(os.path.dirname(INPUT_TARGET_FILE), exist_ok=True)
        with open(INPUT_TARGET_FILE, "w", encoding="utf-8") as f:
            f.write(target)

    def _target_label(self) -> str:
        return "CLIPBOARD" if self._load_input_target() == "clipboard" else "TYPE"

    def _on_target_toggle(self, _widget):
        new_target = "clipboard" if self._load_input_target() == "type" else "type"
        self._save_input_target(new_target)
        self.target_btn.set_label(self._target_label())

    def _voice_label(self) -> str:
        """Show the active voice name with a speaker icon."""
        try:
            active = voice_toggle.load_voice()
            v = voice_toggle.get_voice(active)
            name = v["name"] if v else active.title()
        except Exception:
            name = "?"
        return f"🔊 {name}"

    def _on_voice_toggle(self, _widget):
        """Cycle to the next unlocked voice and speak a confirmation."""
        try:
            new_voice = voice_toggle.toggle()
            if new_voice:
                self.voice_btn.set_label(self._voice_label())
                voice_toggle.speak(
                    f"Switched to {new_voice}.", voice_id=new_voice
                )
        except Exception:
            pass

    def _build_keys(self):
        for child in self.grid.get_children():
            self.grid.remove(child)
        rows = ROWS_UPPER if self.shift_on else ROWS_LOWER
        for r, row in enumerate(rows):
            for c, key in enumerate(row):
                label = LABELS.get(key, key)
                btn = Gtk.Button(label=label)
                if key in SPECIAL or key == "shift":
                    btn.get_style_context().add_class("special")
                width = 2 if key in ("space",) else 1
                btn.connect("clicked", self._on_key, key)
                self.grid.attach(btn, c, r, width, 1)
        self._build_pins()
        self.grid.show_all()

    def _on_key(self, _widget, key):
        if key == "shift":
            self.shift_on = not self.shift_on
            self._build_keys()
            return
        ctrl, alt, shift = _modifier_state()
        send(key, ctrl=ctrl, alt=alt, shift=shift, target_win=self._focus_target_win)
        if self.shift_on:
            self.shift_on = False
            self._build_keys()

    # -- Pinned snippets: the empty strip to the right of arrows/space on
    # row 4. Left-click types the pinned text out, right-click unpins it.
    # These persist across shift toggles and restarts -- unlike the OS
    # clipboard, pinning one doesn't get wiped out by the next Ctrl+C. --

    def _load_pins(self):
        try:
            with open(PINS_FILE, "r", encoding="utf-8") as f:
                pins = json.load(f)
            if isinstance(pins, list):
                return pins
        except FileNotFoundError:
            return list(DEFAULT_PINS)
        except Exception:
            log.warning("pinned_snippets.json unreadable, ignoring", exc_info=True)
        return []

    def _save_pins(self, pins):
        os.makedirs(os.path.dirname(PINS_FILE), exist_ok=True)
        with open(PINS_FILE, "w", encoding="utf-8") as f:
            json.dump(pins, f, indent=2)

    def _build_pins(self):
        pins = self._load_pins()
        row = len(ROWS_LOWER) - 1  # arrows/space row -- the empty strip
        start_col = 7  # cols 0-6 are left/down/up/right/space(x2)/shift-tab
        for i, pin in enumerate(pins[:PIN_SLOTS]):
            btn = Gtk.Button(label=pin.get("label", pin.get("text", "?"))[:10])
            btn.get_style_context().add_class("pin")
            btn.set_tooltip_text(pin.get("text", ""))
            btn.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            btn.connect("clicked", self._on_pin_clicked, pin.get("text", ""))
            btn.connect("button-press-event", self._on_pin_right_click, i)
            self.grid.attach(btn, start_col + i, row, 1, 1)
        add_btn = Gtk.Button(label="+ pin")
        add_btn.get_style_context().add_class("pin-add")
        add_btn.set_tooltip_text("Pin current clipboard contents")
        add_btn.connect("clicked", self._on_pin_add)
        self.grid.attach(add_btn, start_col + PIN_SLOTS, row, 1, 1)

    def _on_pin_clicked(self, _widget, text):
        if not text:
            return
        focus_guard.guarded_type(self._focus_target_win, text)

    def _on_pin_right_click(self, _widget, event, index):
        if event.button != 3:  # only right-click unpins
            return False
        pins = self._load_pins()
        if 0 <= index < len(pins):
            removed = pins.pop(index)
            self._save_pins(pins)
            self._build_keys()
            try:
                voice_toggle.speak(f"Unpinned {removed.get('label', 'that')}.")
            except Exception:
                pass
        return True

    def _on_pin_add(self, _widget):
        pins = self._load_pins()
        if len(pins) >= PIN_SLOTS:
            try:
                voice_toggle.speak("Pin slots full. Unpin one first.")
            except Exception:
                pass
            return
        try:
            proc = subprocess.run(
                ["xclip", "-selection", "clipboard", "-o"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3,
            )
            text = proc.stdout.decode("utf-8", errors="replace").strip()
        except Exception:
            text = ""
        if not text:
            try:
                voice_toggle.speak("Clipboard is empty, nothing to pin.")
            except Exception:
                pass
            return
        label = text if len(text) <= 10 else text[:9] + "…"
        pins.append({"label": label, "text": text})
        self._save_pins(pins)
        self._build_keys()
        try:
            voice_toggle.speak(f"Pinned {label}.")
        except Exception:
            pass

    def toggle(self):
        GLib.idle_add(self._toggle_main_thread)

    def _toggle_main_thread(self):
        opening = not self.visible_state
        if opening:
            # Snapshot whatever's focused right now -- this is where every
            # keystroke should land for as long as the keyboard stays open,
            # since the keyboard itself never takes focus (set_accept_focus).
            self._focus_target_win = focus_guard.active_window()
        target_y = self.center_y if opening else self.hidden_y
        target_op = 1.0 if opening else 0.0
        self._animate_to(target_y, target_op)
        self.visible_state = opening
        self._apply_input_passthrough(not opening)
        return False

    def _animate_to(self, target_y, target_opacity):
        if self._anim_id:
            GLib.source_remove(self._anim_id)
        steps = {"n": 0}

        def step():
            steps["n"] += 1
            _, cur_y = self.get_position()
            dy = target_y - cur_y
            cur_op = self.get_opacity()
            dop = target_opacity - cur_op
            done = abs(dy) < 4 and abs(dop) < 0.05
            if done or steps["n"] > 20:
                self.move(self.center_x, target_y)
                self.set_opacity(target_opacity)
                self._anim_id = None
                return False
            self.move(self.center_x, int(cur_y + dy * 0.45))
            self.set_opacity(max(0.0, min(1.0, cur_op + dop * 0.45)))
            return True

        self._anim_id = GLib.timeout_add(15, step)

    def _typing_state(self) -> tuple[str, str]:
        """Read typing state file written by ptt_pynput.py."""
        try:
            with open(TYPING_STATE_FILE, "r", encoding="utf-8") as f:
                parts = f.read().strip().split(":")
                state = parts[0]
                mode = parts[1] if len(parts) > 1 else ""
                if state in ("typing", "idle"):
                    return state, mode
        except Exception:
            pass
        return "idle", ""

    def _cursor_position(self) -> tuple[int, int]:
        """Return current mouse pointer position as a proxy for text cursor."""
        try:
            out = subprocess.check_output(
                ["xdotool", "getmouselocation"],
                env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
                stderr=subprocess.DEVNULL,
                timeout=1,
            ).decode()
            x = int(out.split()[0].split(":")[1])
            y = int(out.split()[1].split(":")[1])
            return x, y
        except Exception:
            return self.center_x, self.center_y

    def _show_typing_indicator(self, mode: str):
        labels = {
            "bubbly": "✨  Typing cursive...",
            "bold": "𝐁  Typing bold...",
            "big": "Ｔ  Typing big...",
        }
        self.typing_indicator.set_markup(
            f'<span font="16" weight="bold" color="{HUD_ORANGE}">'
            f'{labels.get(mode, "Typing...")}</span>'
        )
        self.mode_bar.hide()
        self.grid.hide()
        self.typing_indicator.show()
        # Move near cursor; keep window roughly on screen.
        cx, cy = self._cursor_position()
        pad = 20
        x = max(0, min(self.sw - self.WIDTH, cx - self.WIDTH // 2))
        y = max(0, min(self.sh - self.HEIGHT, cy - self.HEIGHT - pad))
        self.move(x, y)
        # Remember user-anchored position so the keyboard returns there after.
        self._anchor_x = self.center_x
        self._anchor_y = self.center_y
        self.center_x = x
        self.center_y = y
        self.hidden_y = y + self.POP_OFFSET

    def _show_keyboard_content(self):
        self.typing_indicator.hide()
        self.mode_bar.show()
        self.grid.show()
        # Return to the last user-anchored position if we were in typing mode.
        if hasattr(self, '_anchor_x'):
            self.center_x = self._anchor_x
            self.center_y = self._anchor_y
            self.hidden_y = self.center_y + self.POP_OFFSET
        self.move(self.center_x, self.center_y if self.visible_state else self.hidden_y)

    def _check_typing_state(self):
        state, mode = self._typing_state()
        currently_typing = self.typing_indicator.get_visible()
        if state == "typing" and not currently_typing:
            self._show_typing_indicator(mode)
        elif state == "idle" and currently_typing:
            self._show_keyboard_content()
        # Re-assert every tick, not just on realize/toggle: same self-healing
        # need as controller-legend.py -- nothing guarantees the input shape
        # survives every GTK/X11 event that could reset it over a long
        # session, and the cost of checking is negligible.
        self._apply_input_passthrough(not self.visible_state)
        return True  # keep polling

    def _apply_input_passthrough(self, passthrough):
        gdkwin = self.get_window()
        if not gdkwin:
            return
        if passthrough:
            self.input_shape_combine_region(cairo.Region())
        else:
            self.input_shape_combine_region(None)
        gdkwin.set_pass_through(passthrough)


PIDFILE = "/tmp/slide_keyboard.pid"

if __name__ == "__main__":
    win = SlideKeyboard()
    win.connect("destroy", Gtk.main_quit)
    # Own our PID file so external togglers always know which process to signal.
    try:
        with open(PIDFILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass
    # ptt_pynput.py owns the F14 listener (it already runs persistently) and
    # toggles us via SIGUSR1 — avoids two competing F14 listeners.
    signal.signal(signal.SIGUSR1, lambda *_: win.toggle())
    if "--show" in sys.argv:
        GLib.idle_add(win.toggle)
    Gtk.main()
