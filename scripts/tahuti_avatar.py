#!/usr/bin/env python3
"""Tahuti's one box — the avatar surface and the approval query are the
SAME window (Lijah's law, 2026-10-31).

There is exactly one top-level window in this module. The avatar lives
in it. When the shared approval gate asks for a decision, the query
grows INSIDE that same box — avatar on the left, the Who/What/Where/
When/Why/How request on the right — and when the operator decides, the
box shrinks back to avatar-only and hides. Edit is an in-box state of
the same window, never a separate dialog. Nothing else in this module
may create a window: a second surface is a bug, not a feature.

The box blocks only Tahuti's requesting thread until the operator
decides or the request expires. Close and timeout deny. Fail closed.
"""

from __future__ import annotations

import os
import sys
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import GdkPixbuf, GLib, Gtk

    GTK_AVAILABLE = True
except Exception:  # pragma: no cover - import-safe on non-desktop CI
    GTK_AVAILABLE = False

from voice_profiles import get_avatar_settings, load_profiles

TIMEOUT_SECONDS = 120
AVATAR_DIR = Path(__file__).resolve().parent.parent / "assets" / "avatars"

# The corner loop (Lijah, 2026-10-31): top-left -> bottom-left ->
# bottom-right. The notification corner is never in the loop.
CORNER_LOOP = ("top-left", "bottom-left", "bottom-right")
DEFAULT_NOTIFICATION_CORNER = "top-right"


def card_details(
    pattern_key: str, description: str, extra: dict[str, Any] | None = None
) -> dict[str, str]:
    """Build the card's fixed trust fields without inventing action details."""
    extra = extra or {}
    action = str(
        extra.get("action")
        or (pattern_key.split(":")[1] if ":" in pattern_key else pattern_key)
    )
    mode = str(extra.get("mode") or "foreground")
    return {
        "who": "Tahuti — your AI Controller",
        "what": description,
        "where": "Your active Linux desktop",
        "when": "Immediately after you choose Yes",
        "why": str(
            extra.get("why")
            or extra.get("reason")
            or "This action needs your approval before Tahuti runs it"
        ),
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


def corners_for(settings: dict[str, Any]) -> list[str]:
    """The corners the box may occupy, notification corner excluded."""
    corners = list(CORNER_LOOP)
    if settings.get("respect_notification_corner", True):
        notification = settings.get("notification_corner", DEFAULT_NOTIFICATION_CORNER)
        if notification in corners:
            corners.remove(notification)
    return corners or list(CORNER_LOOP)


def corner_position(
    corner: str,
    width: int,
    height: int,
    screen_width: int,
    screen_height: int,
    margin: int = 16,
) -> tuple[int, int]:
    """Top-left (x, y) for the box at a corner, clamped on-screen."""
    if corner == "bottom-left":
        return (margin, max(margin, screen_height - height - margin))
    if corner == "bottom-right":
        return (
            max(margin, screen_width - width - margin),
            max(margin, screen_height - height - margin),
        )
    if corner == "top-right":
        return (max(margin, screen_width - width - margin), margin)
    return (margin, margin)  # top-left and any unknown corner


def _avatar_px(settings: dict[str, Any]) -> int:
    try:
        px = int(settings.get("size_px", 220))
    except (TypeError, ValueError):
        px = 220
    try:
        scale = float(settings.get("scale", 1.0))
    except (TypeError, ValueError):
        scale = 1.0
    return max(80, min(400, int(px * scale)))


_CARD_CSS = b"""
  window { background: #101a19; color: #edf5ed; }
  #heading { color: #d7b56d; font-size: 18px; font-weight: bold; }
  #subhead { color: #79c9a1; font-size: 12px; font-weight: bold; }
  #value { color: #edf5ed; font-size: 12px; }
  #effect { color: #d7b56d; font-size: 12px; font-weight: bold; }
  button { border-radius: 8px; padding: 9px; color: #f5ead2; background: #21483c; }
  #deny { background: #552d31; } #session { background: #31584d; }
"""


if GTK_AVAILABLE:

    class TahutiBox:
        """The one box. Created once per process, reused for every query.

        Layout: a horizontal box — avatar pane on the left, a stack on
        the right with three in-box states: "idle" (avatar only),
        "query" (the approval request), "edit" (the edit notice).
        """

        def __init__(self) -> None:
            self._corner_index = 0
            self._active = False
            self._outcome = "deny"
            self._loop = None
            self._timer_id = None

            win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
            self.window = win
            win.set_title("Tahuti")
            win.set_keep_above(True)
            win.set_skip_taskbar_hint(True)
            win.set_skip_pager_hint(True)
            win.set_resizable(True)
            win.connect("delete-event", self._on_delete_event)

            self._css = Gtk.CssProvider()
            self._css.load_from_data(_CARD_CSS)
            self._screen = win.get_screen()
            Gtk.StyleContext.add_provider_for_screen(
                self._screen, self._css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

            root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            root.set_border_width(12)
            win.add(root)

            # Avatar pane — always present; this window is the avatar's home.
            avatar_pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            self._portrait = Gtk.Image()
            avatar_pane.pack_start(self._portrait, False, False, 0)
            name = Gtk.Label(label="TAHUTI")
            name.set_name("heading")
            avatar_pane.pack_start(name, False, False, 0)
            root.pack_start(avatar_pane, False, False, 0)

            self._stack = Gtk.Stack()
            self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
            self._stack.set_transition_duration(150)
            root.pack_start(self._stack, True, True, 0)

            # Idle state — avatar only, no query content.
            idle = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            idle_label = Gtk.Label(label="Standing by")
            idle_label.set_name("subhead")
            idle_label.set_xalign(0)
            idle.pack_start(idle_label, False, False, 0)
            self._stack.add_named(idle, "idle")

            # Query state — the six trust fields plus the decision grid.
            query = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            heading = Gtk.Label(label="TAHUTI  /  APPROVAL REQUEST")
            heading.set_name("heading")
            heading.set_xalign(0)
            query.pack_start(heading, False, False, 0)
            subtitle = Gtk.Label(label="Your decision controls the next action")
            subtitle.set_name("subhead")
            subtitle.set_xalign(0)
            query.pack_start(subtitle, False, False, 0)
            self._fields: dict[str, Any] = {}
            for key, title in (
                ("who", "WHO"),
                ("what", "WHAT"),
                ("where", "WHERE"),
                ("when", "WHEN"),
                ("why", "WHY"),
                ("how", "HOW"),
            ):
                row = Gtk.Box(spacing=8)
                k = Gtk.Label(label=title)
                k.set_name("subhead")
                k.set_width_chars(7)
                k.set_xalign(0)
                v = Gtk.Label(label="")
                v.set_name("value")
                v.set_xalign(0)
                v.set_line_wrap(True)
                row.pack_start(k, False, False, 0)
                row.pack_start(v, True, True, 0)
                query.pack_start(row, False, False, 0)
                self._fields[key] = v
            self._effect = Gtk.Label(label="")
            self._effect.set_name("effect")
            self._effect.set_xalign(0)
            self._effect.set_line_wrap(True)
            query.pack_start(self._effect, False, False, 4)
            grid = Gtk.Grid(column_spacing=8, row_spacing=8)
            for n, (text, value, style) in enumerate(
                (
                    ("Yes", "once", ""),
                    ("Always for this session", "session", "session"),
                    ("Deny", "deny", "deny"),
                    ("Edit", "edit", ""),
                )
            ):
                button = Gtk.Button(label=text)
                if style:
                    button.set_name(style)
                if value == "edit":
                    button.connect("clicked", lambda _b: self._show_edit())
                else:
                    button.connect("clicked", lambda _b, v=value: self._choose(v))
                grid.attach(button, n % 2, n // 2, 1, 1)
            query.pack_end(grid, False, False, 0)
            self._stack.add_named(query, "query")

            # Edit state — in-box, same window. Edit never runs the action.
            edit = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            edit_title = Gtk.Label(label="Edit does not run the action.")
            edit_title.set_name("heading")
            edit_title.set_xalign(0)
            edit.pack_start(edit_title, False, False, 0)
            edit_body = Gtk.Label(
                label="Change the requested task at its source, then Tahuti "
                "will present the revised request."
            )
            edit_body.set_name("value")
            edit_body.set_xalign(0)
            edit_body.set_line_wrap(True)
            edit.pack_start(edit_body, False, False, 0)
            back = Gtk.Button(label="Back to request")
            back.connect(
                "clicked", lambda _b: self._stack.set_visible_child_name("query")
            )
            edit.pack_end(back, False, False, 0)
            self._stack.add_named(edit, "edit")

            self._stack.set_visible_child_name("idle")

        # -- internal state changes -------------------------------------

        def _show_edit(self) -> None:
            if self._active:
                self._stack.set_visible_child_name("edit")

        def _choose(self, value: str) -> None:
            if not self._active:
                return
            self._outcome = value
            if self._timer_id is not None:
                GLib.source_remove(self._timer_id)
                self._timer_id = None
            if self._loop is not None and self._loop.is_running():
                self._loop.quit()

        def _on_delete_event(self, *_args) -> bool:
            # Closing the box is a decision: deny. The box itself is the
            # avatar's home, so it hides instead of being destroyed.
            if self._active:
                self._choose("deny")
            self.window.hide()
            return True

        def _on_timeout(self) -> bool:
            self._timer_id = None
            self._choose("deny")
            return False

        def _place(self, settings: dict[str, Any], width: int, height: int) -> None:
            corners = corners_for(settings)
            if settings.get("movement", "corner_loop") == "corner_loop":
                corner = corners[self._corner_index % len(corners)]
                self._corner_index += 1
            else:
                corner = corners[0]
            try:
                screen_w = self._screen.get_width()
                screen_h = self._screen.get_height()
            except Exception:
                return
            x, y = corner_position(corner, width, height, screen_w, screen_h)
            self.window.move(x, y)

        # -- the one public act ------------------------------------------

        def present(
            self,
            card: dict[str, str],
            settings: dict[str, Any],
            timeout: int = TIMEOUT_SECONDS,
        ) -> str:
            """Show the query inside this box; return once/session/deny."""
            px = _avatar_px(settings)
            portrait = _avatar_path()
            if portrait:
                try:
                    self._portrait.set_from_pixbuf(
                        GdkPixbuf.Pixbuf.new_from_file_at_scale(
                            str(portrait), px, px, True
                        )
                    )
                except Exception:
                    self._portrait.clear()
            else:
                self._portrait.clear()
            for key, label in self._fields.items():
                label.set_text(card[key])
            self._effect.set_text(card["effect"])

            idle_size = (px + 40, px + 76)
            query_size = (px + 392, max(px + 76, 392))
            self._outcome = "deny"
            self._active = True
            self._stack.set_visible_child_name("query")
            self.window.resize(*query_size)
            self._place(settings, *query_size)
            self.window.show_all()
            self.window.present()
            self._loop = GLib.MainLoop()
            self._timer_id = GLib.timeout_add_seconds(timeout, self._on_timeout)
            try:
                self._loop.run()
            finally:
                self._loop = None
                if self._timer_id is not None:
                    GLib.source_remove(self._timer_id)
                    self._timer_id = None
                self._active = False
                self._stack.set_visible_child_name("idle")
                self.window.resize(*idle_size)
                self.window.hide()
            return self._outcome

        def destroy(self) -> None:
            """Tear the box down for real (tests, process shutdown)."""
            if self._timer_id is not None:
                GLib.source_remove(self._timer_id)
                self._timer_id = None
            try:
                Gtk.StyleContext.remove_provider_for_screen(self._screen, self._css)
            except Exception:
                pass
            self.window.destroy()

else:  # pragma: no cover
    TahutiBox = None  # type: ignore


@dataclass
class _PendingRequest:
    card: dict[str, str]
    settings: dict[str, Any]
    completed: threading.Event
    verdict: str = "deny"


_BOX: Any = None
_PROMPT_ACTIVE = False
_PROMPT_LOCK = threading.RLock()
_PENDING: deque[_PendingRequest] = deque()


def _resolve_pending_as_denied() -> None:
    with _PROMPT_LOCK:
        while _PENDING:
            request = _PENDING.popleft()
            request.verdict = "deny"
            request.completed.set()


def _get_box() -> Any:
    global _BOX
    if _BOX is None:
        _BOX = TahutiBox()
    return _BOX


def _reset_box() -> None:
    """Destroy and forget the singleton box (test support)."""
    global _BOX
    if _BOX is not None:
        try:
            _BOX.destroy()
        except Exception:
            pass
        _BOX = None


def prompt(pattern_key: str, description: str, **extra: Any) -> str:
    """Present queued requests one at a time in Tahuti's single window."""
    global _PROMPT_ACTIVE
    if not GTK_AVAILABLE or not os.environ.get("DISPLAY"):
        return "deny"
    try:
        settings = get_avatar_settings(load_profiles())
        if not settings.get("approval_box", True) or not settings.get(
            "popup_on_approval", True
        ):
            return "deny"
        request = _PendingRequest(
            card_details(pattern_key, description, extra), settings, threading.Event()
        )
        with _PROMPT_LOCK:
            driver = not _PROMPT_ACTIVE
            _PENDING.append(request)
            if driver:
                _PROMPT_ACTIVE = True
        if not driver:
            request.completed.wait()
            return request.verdict
        while True:
            with _PROMPT_LOCK:
                if not _PENDING:
                    _PROMPT_ACTIVE = False
                    return request.verdict
                active = _PENDING.popleft()
            try:
                active.verdict = _get_box().present(
                    active.card, active.settings, TIMEOUT_SECONDS
                )
            except Exception:
                active.verdict = "deny"
            finally:
                active.completed.set()
    except Exception:
        _resolve_pending_as_denied()
        return "deny"
