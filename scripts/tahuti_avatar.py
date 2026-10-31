#!/usr/bin/env python3
"""Tahuti's native desktop approval card for the AI Controller bridge.

The card is deliberately small and non-modal to the desktop. It blocks only
Tahuti's requesting thread until the operator makes a decision or it expires.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GdkPixbuf, GLib, Gtk
    GTK_AVAILABLE = True
except Exception:  # pragma: no cover - import-safe on non-desktop CI
    GTK_AVAILABLE = False

from voice_profiles import get_avatar_settings, load_profiles

TIMEOUT_SECONDS = 120
AVATAR_DIR = Path(__file__).resolve().parent.parent / "assets" / "avatars"


def card_details(pattern_key: str, description: str, extra: dict[str, Any] | None = None) -> dict[str, str]:
    """Build the card's fixed trust fields without inventing action details."""
    extra = extra or {}
    action = str(extra.get("action") or (pattern_key.split(":")[1] if ":" in pattern_key else pattern_key))
    mode = str(extra.get("mode") or "foreground")
    return {
        "who": "Tahuti — your AI Controller",
        "what": description,
        "where": "Your active Linux desktop",
        "when": "Immediately after you choose Yes",
        "why": str(extra.get("why") or extra.get("reason") or "This action needs your approval before Tahuti runs it"),
        "how": f"{action} through the Tahuti desktop bridge ({mode})",
        "effect": "Nothing runs until you choose Yes or Always for this session.",
        "pattern_key": pattern_key,
    }


def _avatar_path() -> Path | None:
    configured = get_avatar_settings(load_profiles()).get("active_avatar", "tahuti")
    for suffix in (".webp", ".png", ".jpg"):
        candidate = AVATAR_DIR / f"{configured}{suffix}"
        if candidate.exists():
            return candidate
    return None


def prompt(pattern_key: str, description: str, **extra: Any) -> str:
    """Return once/session/deny. Close and timeout deny. Fail closed."""
    if not GTK_AVAILABLE or not os.environ.get("DISPLAY"):
        return "deny"
    try:
        return _prompt_gtk(card_details(pattern_key, description, extra))
    except Exception:
        return "deny"


def _prompt_gtk(card: dict[str, str]) -> str:
    settings = get_avatar_settings(load_profiles())
    if not settings.get("approval_box", True) or not settings.get("popup_on_approval", True):
        return "deny"
    win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    win.set_title("Tahuti — Your decision")
    win.set_keep_above(True)
    win.set_skip_taskbar_hint(True)
    win.set_default_size(520, 360)
    win.set_resizable(False)
    win.set_decorated(True)

    css = Gtk.CssProvider()
    css.load_from_data(b"""
      window { background: #101a19; color: #edf5ed; }
      #heading { color: #d7b56d; font-size: 18px; font-weight: bold; }
      #subhead { color: #79c9a1; font-size: 12px; font-weight: bold; }
      #value { color: #edf5ed; font-size: 12px; }
      #effect { color: #d7b56d; font-size: 12px; font-weight: bold; }
      button { border-radius: 8px; padding: 9px; color: #f5ead2; background: #21483c; }
      #deny { background: #552d31; } #session { background: #31584d; }
    """)
    screen = win.get_screen()
    Gtk.StyleContext.add_provider_for_screen(screen, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    outer.set_border_width(14)
    win.add(outer)
    header = Gtk.Box(spacing=10)
    portrait = _avatar_path()
    if portrait:
        image = Gtk.Image.new_from_pixbuf(GdkPixbuf.Pixbuf.new_from_file_at_scale(str(portrait), 78, 78, True))
        header.pack_start(image, False, False, 0)
    titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    label = Gtk.Label(label="TAHUTI  /  APPROVAL REQUEST")
    label.set_name("heading"); label.set_xalign(0); titles.pack_start(label, False, False, 0)
    subtitle = Gtk.Label(label="Your decision controls the next action")
    subtitle.set_name("subhead"); subtitle.set_xalign(0); titles.pack_start(subtitle, False, False, 0)
    header.pack_start(titles, True, True, 0)
    outer.pack_start(header, False, False, 0)
    for key, title in (("who", "WHO"), ("what", "WHAT"), ("where", "WHERE"), ("when", "WHEN"), ("why", "WHY"), ("how", "HOW")):
        row = Gtk.Box(spacing=8)
        k = Gtk.Label(label=title); k.set_name("subhead"); k.set_width_chars(7); k.set_xalign(0)
        v = Gtk.Label(label=card[key]); v.set_name("value"); v.set_xalign(0); v.set_line_wrap(True)
        row.pack_start(k, False, False, 0); row.pack_start(v, True, True, 0); outer.pack_start(row, False, False, 0)
    effect = Gtk.Label(label=card["effect"]); effect.set_name("effect"); effect.set_xalign(0); effect.set_line_wrap(True)
    outer.pack_start(effect, False, False, 4)

    outcome = {"value": "deny"}
    loop = GLib.MainLoop()
    timer = {"id": None}
    def choose(value: str) -> None:
        outcome["value"] = value
        if timer["id"] is not None:
            GLib.source_remove(timer["id"]); timer["id"] = None
        if loop.is_running(): loop.quit()
    grid = Gtk.Grid(column_spacing=8, row_spacing=8)
    for n, (text, value, style) in enumerate((("Yes", "once", ""), ("Always for this session", "session", "session"), ("Deny", "deny", "deny"), ("Edit", "edit", ""))):
        button = Gtk.Button(label=text)
        if style: button.set_name(style)
        if value == "edit":
            button.connect("clicked", lambda _b: _show_edit_notice(win))
        else:
            button.connect("clicked", lambda _b, v=value: choose(v))
        grid.attach(button, n % 2, n // 2, 1, 1)
    outer.pack_end(grid, False, False, 0)
    win.connect("delete-event", lambda *_: choose("deny") or True)
    def _on_timeout() -> bool:
        timer["id"] = None
        choose("deny")
        return False
    win.show_all()
    timer["id"] = GLib.timeout_add_seconds(TIMEOUT_SECONDS, _on_timeout)
    loop.run()
    win.destroy()
    Gtk.StyleContext.remove_provider_for_screen(screen, css)
    return outcome["value"]


def _show_edit_notice(parent: Any) -> None:
    dialog = Gtk.MessageDialog(transient_for=parent, modal=False, message_type=Gtk.MessageType.INFO,
                               buttons=Gtk.ButtonsType.CLOSE, text="Edit does not run the action.")
    dialog.format_secondary_text("Change the requested task at its source, then Tahuti will present the revised request.")
    dialog.connect("response", lambda d, *_: d.destroy())
    dialog.show_all()
