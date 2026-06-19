#!/usr/bin/env python3
"""
Controller Legend HUD — horizontal strip below cursor with smoke pointer.
"""
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GLib
import cairo, os, glob, subprocess

PROFILE_STATE = os.path.expanduser("~/.controller_current_profile")

LAYOUTS = {
    "desktop": [
        ("A",   "Click"),
        ("B",   "Bksp"),
        ("X",   "Del"),
        ("Y",   "Super"),
        ("LB",  "Shift"),
        ("RB",  "R·Clk"),
        ("LT",  "Ctrl"),
        ("RT",  "Talk"),
        ("View","Kbd"),
        ("≡",   "Tab"),
        ("Logo","W·Tab"),
        ("LS",  "Space"),
        ("RS",  "Enter"),
        ("D↕",  "Arrows"),
        ("_",   "Undrsc"),
    ],
    "browser": [
        ("A",   "Click"),
        ("B",   "Back"),
        ("X",   "Reload"),
        ("Y",   "New Tab"),
        ("○●",  "Address"),
        ("●○",  "Bookmark"),
        ("LB",  "← Tab"),
        ("RB",  "Tab →"),
        ("LT",  "R·Clk"),
        ("RT",  "Talk"),
        ("L3",  "Space"),
        ("D↔",  "Bk/Fwd"),
    ],
    "iptv": [
        ("A",   "▶ ‖"),
        ("B",   "Stop"),
        ("X",   "Info"),
        ("Y",   "Full"),
        ("○●",  "Menu"),
        ("●○",  "Guide"),
        ("LB",  "Ch ↑"),
        ("RB",  "Ch ↓"),
        ("LT",  "◀◀"),
        ("RT",  "▶▶"),
        ("L3",  "Space"),
        ("D↕",  "Ch/Vol"),
    ],
}

POINTER_H = 10  # height of the smoke triangle above the box

CSS = b"""
window {
    background-color: transparent;
}
"""

class Legend(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.set_keep_above(True)
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_app_paintable(True)

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)

        css = Gtk.CssProvider()
        css.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            screen, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.connect("draw", self.on_draw)
        self.connect("realize", self._make_clickthrough)
        self.connect("map-event", self._make_clickthrough)

        # Outer box with padding for the pointer triangle
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.set_margin_top(POINTER_H)
        self.add(outer)

        # Main strip
        self.grid = Gtk.Grid()
        self.grid.set_column_spacing(12)
        self.grid.set_row_spacing(2)
        self.grid.set_margin_start(10)
        self.grid.set_margin_end(10)
        self.grid.set_margin_top(6)
        self.grid.set_margin_bottom(6)
        outer.pack_start(self.grid, False, False, 0)

        self.btn_labels = []
        self.act_labels = []
        for i, (b, a) in enumerate(LAYOUTS["desktop"]):
            bl = Gtk.Label(label=b)
            bl.set_markup(f'<span font_family="monospace" weight="bold" '
                          f'font_size="9000" foreground="#FF6A00">{b}</span>')
            bl.set_halign(Gtk.Align.CENTER)
            self.grid.attach(bl, i, 0, 1, 1)

            al = Gtk.Label(label=a)
            al.set_markup(f'<span font_family="monospace" '
                          f'font_size="7500" foreground="#aaaaaa">{a}</span>')
            al.set_halign(Gtk.Align.CENTER)
            self.grid.attach(al, i, 1, 1, 1)

            self.btn_labels.append(bl)
            self.act_labels.append(al)

        # Profile badge top-right
        self.mode_lbl = Gtk.Label()
        self.mode_lbl.set_markup(
            '<span font_family="monospace" weight="bold" '
            'font_size="7000" foreground="#FF6A00">DESKTOP</span>')
        self.mode_lbl.set_halign(Gtk.Align.END)
        outer.pack_start(self.mode_lbl, False, False, 0)

        self._profile = ""
        self._cx = 0  # cursor x relative to window for pointer
        self.show_all()
        GLib.timeout_add(100, self.tick)

    def _make_clickthrough(self, widget, event=None):
        region = cairo.Region()
        self.input_shape_combine_region(region)
        gdkwin = self.get_window()
        if gdkwin:
            gdkwin.set_pass_through(True)

    def on_draw(self, widget, cr):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()

        # Clear
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        # Main box (below pointer)
        bx, by = 0, POINTER_H
        bw, bh = w, h - POINTER_H

        # Background
        cr.set_source_rgba(0.05, 0.05, 0.11, 0.72)
        self._rounded_rect(cr, bx, by, bw, bh, 8)
        cr.fill()

        # Orange border
        cr.set_source_rgba(1.0, 0.416, 0.0, 0.85)
        cr.set_line_width(1.2)
        self._rounded_rect(cr, bx + 0.6, by + 0.6, bw - 1.2, bh - 1.2, 8)
        cr.stroke()

        # Smoke triangle pointer — tip at cursor x, base on top of box
        tip_x = max(16, min(self._cx, w - 16))
        cr.set_source_rgba(0.05, 0.05, 0.11, 0.72)
        cr.move_to(tip_x, 0)
        cr.line_to(tip_x - 8, POINTER_H)
        cr.line_to(tip_x + 8, POINTER_H)
        cr.close_path()
        cr.fill()

        # Triangle outline
        cr.set_source_rgba(1.0, 0.416, 0.0, 0.85)
        cr.set_line_width(1.0)
        cr.move_to(tip_x, 1)
        cr.line_to(tip_x - 7, POINTER_H)
        cr.move_to(tip_x + 7, POINTER_H)
        cr.line_to(tip_x, 1)
        cr.stroke()

        return False

    def _rounded_rect(self, cr, x, y, w, h, r):
        cr.arc(x + r, y + r, r, 3.14, 1.5 * 3.14)
        cr.arc(x + w - r, y + r, r, 1.5 * 3.14, 0)
        cr.arc(x + w - r, y + h - r, r, 0, 0.5 * 3.14)
        cr.arc(x + r, y + h - r, r, 0.5 * 3.14, 3.14)
        cr.close_path()

    def update_content(self, profile):
        if profile == self._profile:
            return
        self._profile = profile
        layout = LAYOUTS.get(profile, LAYOUTS["desktop"])
        self.mode_lbl.set_markup(
            f'<span font_family="monospace" weight="bold" '
            f'font_size="7000" foreground="#FF6A00">{profile.upper()}</span>')
        for i, (bl, al) in enumerate(zip(self.btn_labels, self.act_labels)):
            if i < len(layout):
                b, a = layout[i]
                bl.set_markup(f'<span font_family="monospace" weight="bold" '
                               f'font_size="9000" foreground="#FF6A00">{b}</span>')
                al.set_markup(f'<span font_family="monospace" '
                               f'font_size="7500" foreground="#aaaaaa">{a}</span>')
            else:
                bl.set_markup("")
                al.set_markup("")

    def tick(self):
        # Auto-detect: hide when no controller plugged in
        controller_present = bool(glob.glob('/dev/input/js*'))
        if not controller_present:
            if self.get_visible():
                self.hide()
            return True
        if not self.get_visible():
            self.show_all()
            self._make_clickthrough(self)  # re-apply after show

        display = Gdk.Display.get_default()
        seat = display.get_default_seat()
        ptr = seat.get_pointer()
        _, cx, cy = ptr.get_position()

        w, h = self.get_size()
        sw = self.get_screen().get_width()
        sh = self.get_screen().get_height()

        OFFSET_X = 60   # pixels right of cursor
        OFFSET_Y = 70   # pixels below cursor (gives ~half-inch clearance)

        # If legend would go off the bottom, flip it above the cursor
        if cy + OFFSET_Y + h + 4 > sh:
            py = max(4, cy - h - 10)
        else:
            py = cy + OFFSET_Y

        px = min(cx + OFFSET_X, sw - w - 4)
        px = max(0, px)

        self._cx = cx - px  # pointer offset within window
        self.move(px, py)
        self.queue_draw()
        self.update_content(get_profile())
        return True


def get_profile():
    try:
        return open(PROFILE_STATE).read().strip()
    except:
        return "desktop"


if __name__ == "__main__":
    win = Legend()
    win.connect("destroy", Gtk.main_quit)
    Gtk.main()
