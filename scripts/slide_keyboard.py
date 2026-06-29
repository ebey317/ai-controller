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

import os
import signal
import subprocess
import sys
import warnings

# GTK3 deprecation noise is not useful in production.
warnings.filterwarnings("ignore", category=DeprecationWarning)

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ai_controller_paths import config_dir, ensure_config_dir

# Shared with ptt_pynput.py: PRO = plain text, BUBBLY = cursive + emoji
ensure_config_dir()
PTT_MODE_FILE = os.path.join(config_dir(), "ptt_mode")
INPUT_TARGET_FILE = os.path.join(config_dir(), "ai_controller_input_target")
TYPING_STATE_FILE = "/tmp/ptt_typing_state"

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
    "esc": "Escape",
    "bksp": "BackSpace",
    "tab": "Tab",
    "enter": "Return",
    "space": "space",
    "left": "Left",
    "right": "Right",
    "up": "Up",
    "down": "Down",
    "⇧tab": "shift+Tab",
}
LABELS = {
    "esc": "Esc",
    "bksp": "⌫",
    "tab": "Tab ⇥",
    "enter": "Enter ↵",
    "space": "Space",
    "left": "←",
    "right": "→",
    "up": "↑",
    "down": "↓",
    "shift": "⇧",
    "⇧tab": "⇧Tab\n(cycle)",
}


# Same dark/orange family as controller-legend.py's HUD, so the two read as
# one floating-overlay system instead of unrelated widgets.
def _modifier_state():
    """Return (ctrl, alt) from live X11 keyboard state — queried at click time, no listener."""
    try:
        try:
            keymap = Gdk.Keymap.get_for_display(Gdk.Display.get_default())
        except Exception:
            keymap = Gdk.Keymap.get_default()
        mask = keymap.get_modifier_state()
        return (
            bool(mask & Gdk.ModifierType.CONTROL_MASK),
            bool(mask & Gdk.ModifierType.MOD1_MASK),
        )
    except Exception:
        return False, False


HUD_ORANGE = "#FF6A00"

CSS = b"""
window { background-color: transparent; }
#panel {
    background-color: rgba(13,13,18,0.94);
    border: 2px solid #FF6A00;
    border-radius: 16px;
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
"""


def send(key, ctrl=False, alt=False):
    if key == "shift":
        return
    if ctrl or alt:
        prefix = ("ctrl+" if ctrl else "") + ("alt+" if alt else "")
        if key in SPECIAL:
            subprocess.run(["xdotool", "key", prefix + SPECIAL[key]], check=False)
        else:
            subprocess.run(["xdotool", "key", prefix + key.lower()], check=False)
    elif key in SPECIAL:
        subprocess.run(
            ["xdotool", "key", "--clearmodifiers", SPECIAL[key]], check=False
        )
    else:
        subprocess.run(["xdotool", "type", "--clearmodifiers", key], check=False)


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
            screen, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.shift_on = False

        # Outer styled panel (the rounded orange-bordered box) wraps the key
        # grid, matching the legend HUD's visual language.
        self.panel = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.panel.set_name("panel")
        self.panel.set_margin_start(10)
        self.panel.set_margin_end(10)
        self.panel.set_margin_top(10)
        self.panel.set_margin_bottom(10)
        self.add(self.panel)

        # LEFT COLUMN: mode bar across the top + key grid below it.
        self.left_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.panel.pack_start(self.left_col, True, True, 0)

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
        self.panel.pack_start(self.typing_indicator, True, True, 0)
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

        # Poll typing state from ptt_pynput.py so the keyboard can transform
        # into a typing indicator while Unicode modes emit.
        self._typing_poll_id = GLib.timeout_add(100, self._check_typing_state)

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
        self.grid.show_all()

    def _on_key(self, _widget, key):
        if key == "shift":
            self.shift_on = not self.shift_on
            self._build_keys()
            return
        ctrl, alt = _modifier_state()
        send(key, ctrl=ctrl, alt=alt)
        if self.shift_on:
            self.shift_on = False
            self._build_keys()

    def toggle(self):
        GLib.idle_add(self._toggle_main_thread)

    def _toggle_main_thread(self):
        opening = not self.visible_state
        target_y = self.center_y if opening else self.hidden_y
        target_op = 1.0 if opening else 0.0
        self._animate_to(target_y, target_op)
        self.visible_state = opening
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

    def _show_keyboard_content(self):
        self.typing_indicator.hide()
        self.mode_bar.show()
        self.grid.show()
        self.move(self.center_x, self.center_y if self.visible_state else self.hidden_y)

    def _check_typing_state(self):
        state, mode = self._typing_state()
        currently_typing = self.typing_indicator.get_visible()
        if state == "typing" and not currently_typing:
            self._show_typing_indicator(mode)
        elif state == "idle" and currently_typing:
            self._show_keyboard_content()
        return True  # keep polling


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
