#!/usr/bin/env python3
"""The Tahuti run card for EVERY Sensei slash command (2026-11-03).

    "wire that in for every slash command. not just models."

    python3 tahuti_choice_presenter.py --send "/hub"

Sends the command to the LIVE Sensei TUI (via sensei_slash), then adapts the
card to whatever Sensei actually shows. It never reimplements a command — the
card MIRRORS Sensei's screen and forwards presses to it:

  picker  (/model …)   list + cursor      ▲ Up  ▼ Down  ✔ Select  ✖ Cancel
  pager   (/hub /help) page text          ◀ Back  Next ▶  ✖ Close   (digits = #=pick)
  output  (/doctor …)  the printed text   ✔ Done; speaks a digest and closes
                                          when the voice finishes (no timer)

Keyboard (when the card has focus): arrows / j k, Enter, Esc, digits, n b q.
Always leaves Sensei at its idle prompt afterwards (never strands it in a menu:
the next command would be taken as chat and the model would act on it).

Spawned by the goal runner (a headless daemon cannot own a GTK window). The
LAST stdout line is one JSON object:
    {"ok": true,  "mode": "picker"|"pager"|"output", "action": ..., ...}
    {"ok": false, "action": "cancelled"|"unavailable"|"error", "reason": "..."}
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import textwrap
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, os.path.expanduser("~/master-ai-cli"))

import sensei_slash as sb  # noqa: E402

OVERALL_TIMEOUT_S = 180
MAX_LEVELS = 4  # provider -> model -> (variant) -> ...
OUTPUT_CARD_LINES = 22
NO_SPEECH_GRACE_S = 12.0  # output card: give up waiting for the voice after this

_ITEM_RE = re.compile(r"^[\(\[]([ *xX✓])[\)\]]\s+(.*?)(?:\s+[\^v])?$")
_TITLE_BAR_RE = re.compile(r"\|\s*(.+?)\s*\|")  # "┌───|  Select Provider  |───┐" / "|  OPENCODE Models  |"
_SGR_RE = re.compile(r"\x1b\[[0-9;]*m")
# Sensei draws the CURSOR as a coloured background on the row's bullet; the
# plain-text "*" only marks the currently ACTIVE choice and never moves.
_CURSOR_BULLET_RE = re.compile(r"\x1b\[48;2;\d+;\d+;\d+m[\(\[](?:\*| |x|X)[\)\]]")
_MODEL_FOOTER_RE = re.compile(r"MODEL:\s*([^\s·│|]+)")
_CHAT_TITLE_RE = re.compile(r"┌[─\s]*\|\s*[^|]*\d{6,}[^|]*\|")  # the input-box title ("… 1793745641 ○")


_LOG = Path.home() / ".tahuti" / "slash_card.log"


def log(msg: str) -> None:
    try:
        _LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} [{os.getpid()}] {msg}\n")
    except Exception:
        pass


def _out(obj: dict) -> int:
    log(f"RESULT {json.dumps(obj)[:300]}")
    print(json.dumps(obj), flush=True)
    return 0 if obj.get("ok") else 2


def capture_color(pane: str) -> str:
    """Screen WITH colour codes — the cursor row is only visible that way."""
    return sb._tmux("capture-pane", "-p", "-e", "-t", pane, "-S", "-60").stdout


# --------------------------------------------------------------------------
# reading Sensei's screen
# --------------------------------------------------------------------------
def parse_picker(screen: str) -> dict | None:
    """Bottom-most picker box -> {title, items, cursor, active}. Pass colour
    text (capture_color) to get the true cursor row."""
    raw_lines = screen.splitlines()
    plain = [_SGR_RE.sub("", l) for l in raw_lines]
    start = -1
    itop = sb.input_box_top(plain)
    for i in range(len(plain) - 1, -1, -1):
        if i != itop and "┌" in plain[i] and _TITLE_BAR_RE.search(plain[i]) and not _CHAT_TITLE_RE.search(plain[i]):
            start = i
            break
    if start < 0:
        return None
    title = _TITLE_BAR_RE.search(plain[start]).group(1).strip()  # type: ignore[union-attr]
    items: list[str] = []
    active = 0
    cursor = -1
    for j in range(start + 1, len(plain)):
        if "└" in plain[j]:
            break
        m = _ITEM_RE.match(plain[j].strip(sb._BOX + " "))  # keep inner double-spaces: they split label from note
        if m:
            if m.group(1).strip():
                active = len(items)
            if cursor < 0 and _CURSOR_BULLET_RE.search(raw_lines[j]):
                cursor = len(items)
            items.append(m.group(2).strip())
    if not items:
        return None
    return {"title": title, "items": items, "cursor": cursor if cursor >= 0 else active, "active": active}


def render_picker(pk: dict, trail: list[str], width_rows: int = 6) -> str:
    items, cur, act = pk["items"], pk["cursor"], pk.get("active", -1)
    lo = max(0, min(cur - width_rows // 2, max(0, len(items) - width_rows)))
    window = items[lo:lo + width_rows]
    # Marker columns, so long names never wrap: ▶ = cursor, ● = currently active.
    rows = [f"{'▶' if lo + i == cur else ' '} {'●' if lo + i == act else ' '}  {label}"
            for i, label in enumerate(window)]
    head = pk["title"].upper() + (f"   ({' › '.join(trail)})" if trail else "")
    more_up = "   ▲ more above" if lo > 0 else ""
    more_dn = "   ▼ more below" if lo + width_rows < len(items) else ""
    return "\n".join([head, "", *([more_up] if more_up else []), *rows,
                      *([more_dn] if more_dn else []), "",
                      f"{cur + 1} of {len(items)}   ·   ▶ cursor   ● current"])


def wait_for_change(pane: str, before: dict | None, timeout: float = 3.0) -> dict | None:
    """Sensei redraws slowly (>0.6s seen): poll until the picker differs from
    `before` (cursor moved / new level / closed) or the timeout passes."""
    end = time.time() + timeout
    cur = parse_picker(capture_color(pane))
    while time.time() < end:
        if (cur is None) != (before is None) or (cur and before and (
                cur["cursor"] != before["cursor"] or cur["title"] != before["title"]
                or cur["items"] != before["items"])):
            return cur
        time.sleep(0.2)
        cur = parse_picker(capture_color(pane))
    return cur


def pager_text(screen: str, max_lines: int = 24) -> str:
    """The current page of a pager/menu. Prefer the page box itself (from its
    top border to the `n=next b=back q=close` footer); fall back to everything
    above the input box."""
    plain = [_SGR_RE.sub("", l) for l in screen.splitlines()]
    end = sb.input_box_top(plain)
    foot = -1
    for i in range(end - 1, -1, -1):
        if sb._PAGER_RE.search(plain[i]):
            foot = i
            break
    if foot > 0:
        top = -1
        for i in range(foot - 1, -1, -1):
            if "╔" in plain[i]:
                top = i
                break
        if top >= 0:
            page = [sb._clean_line(l) for l in plain[top:foot]]
            page = [l for l in page if l and set(l) - set("─━═╔╗╚╝╠╣║│ ")]
            if page:
                return "\n".join(page[:max_lines])
    body = [sb._clean_line(l) for l in plain[:end]]
    body = [l for l in body if l and set(l) - set("─━═╔╗╚╝╠╣║│ ")
            and not sb._HEADER_RE.search(l) and l.strip(" |") != "chat"]
    return "\n".join(body[-max_lines:])


def tts_playing() -> bool:
    """A LIVE TTS player (exact process names; zombies and shells that merely
    mention the pattern don't count)."""
    pids: list[str] = []
    try:
        pids += subprocess.run(["pgrep", "-x", "hermes_tts_play"], capture_output=True, text=True).stdout.split()
        for pid in subprocess.run(["pgrep", "-x", "mpv"], capture_output=True, text=True).stdout.split():
            try:
                if b"AI_TTS_BARGE" in Path(f"/proc/{pid}/cmdline").read_bytes():
                    pids.append(pid)
            except Exception:
                continue
    except Exception:
        return False
    for pid in pids:
        try:
            if Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z":
                return True
        except Exception:
            continue
    return False


def speak(text: str) -> bool:
    """POST to the voice bridge, same endpoint/auth the runner uses."""
    if not text:
        return False
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    key = os.environ.get("VOICE_BRIDGE_API_KEY", "")
    if key:
        headers["x-api-key"] = key
    try:
        base = os.environ.get("BRIDGE_URL", "http://127.0.0.1:8002")
        req = urllib.request.Request(f"{base}/speak", urllib.parse.urlencode({"text": text}).encode(),
                                     headers=headers, method="POST")
        urllib.request.urlopen(req, timeout=10).read()
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# the card (one overlay, reused for every mode)
# --------------------------------------------------------------------------
class Card:
    def __init__(self) -> None:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gdk, GLib
        from ninja.state_machine import StatePayload, ThootieState, get_overlay
        self.GLib, self.Gdk = GLib, Gdk
        self.ThootieState, self.StatePayload = ThootieState, StatePayload
        self.overlay = get_overlay(agent="tahuti")
        if self.overlay is None:
            raise RuntimeError("no display / overlay")
        self._key_id = None
        self._opened = False
        self.on_key = None  # set per mode: fn(keyname) -> bool

    def open(self, text: str) -> None:
        ov = self.overlay
        ov.set_agent("tahuti")
        self.text(text)
        ov.machine.set_state(self.ThootieState.APPROVAL_INCOMING, ov.machine.payload if ov.machine.payload else None)
        if not ov._win.get_visible():
            ov.show()
        ov._set_active(True)
        ov.machine.set_state(self.ThootieState.APPROVAL_PRESENTED, self.StatePayload(action_kind="approval"))
        self._key_id = ov._win.connect("key-press-event", self._key)
        ov._win.add_events(self.Gdk.EventMask.SCROLL_MASK)
        self._wheel_id = ov._win.connect("scroll-event", self._wheel)
        self._opened = True
        self._grab_keyboard()

    def _grab_keyboard(self, tries: int = 12) -> None:
        """The card is a focus-less popup, so keys never reach it by themselves.
        Take the keyboard while it is open (released in close(), and the card
        always times out), so Up/Down/Left/Right/Enter/Esc — typed or sent by the
        controller's D-pad — drive the card instead of the window behind it."""
        Gdk = self.Gdk

        def attempt() -> bool:
            try:
                gw = self.overlay._win.get_window()
                if gw is not None and gw.is_viewable():
                    seat = Gdk.Display.get_default().get_default_seat()
                    st = seat.grab(gw, Gdk.SeatCapabilities.KEYBOARD, False, None, None, None)
                    self._grabbed = st == Gdk.GrabStatus.SUCCESS
                    log(f"keyboard grab: {st.value_nick}")
                    return False
            except Exception as exc:
                log(f"keyboard grab failed: {exc}")
                return False
            attempt.n += 1  # type: ignore[attr-defined]
            return attempt.n < tries  # type: ignore[attr-defined]

        attempt.n = 0  # type: ignore[attr-defined]
        if attempt():
            self.GLib.timeout_add(100, attempt)

    def _release_keyboard(self) -> None:
        if getattr(self, "_grabbed", False):
            try:
                self.Gdk.Display.get_default().get_default_seat().ungrab()
            except Exception:
                pass
            self._grabbed = False

    def _key(self, _w, event) -> bool:
        name = self.Gdk.keyval_name(event.keyval) or ""
        if name in ("Page_Up", "Page_Down", "Home", "End") and self.overflow():
            self.scroll({"Page_Up": -(self.ROWS - 2), "Page_Down": self.ROWS - 2,
                         "Home": -9999, "End": 9999}[name])
            return True
        return bool(self.on_key(name)) if self.on_key else False

    def _wheel(self, _w, event) -> bool:
        d = {self.Gdk.ScrollDirection.UP: -3, self.Gdk.ScrollDirection.DOWN: 3}.get(event.direction)
        if d is None or not self.overflow():
            return False
        self.scroll(d)
        return True

    # The card is 420x280: about 33 monospace columns and 11 rows of body text.
    # Anything longer is wrapped HERE and shown a window at a time with a visible
    # "▲▼ 1-10/27" indicator — GTK's own scrollbar is the invisible overlay kind,
    # so nobody knew a card could scroll.
    COLS = 33
    ROWS = 11

    def _wrap(self, t: str) -> list[str]:
        rows: list[str] = []
        for line in t.split("\n"):
            if not line.strip():
                rows.append("")
                continue
            lead = min(len(line) - len(line.lstrip()), 6)
            rows += textwrap.wrap(line, self.COLS, subsequent_indent=" " * lead,
                                  replace_whitespace=False, break_long_words=True) or [""]
        return rows

    def text(self, t: str) -> None:
        rows = self._wrap(t)
        if t != getattr(self, "_full", None):
            self._off = 0
        self._full, self._rows = t, rows
        self._render()

    def overflow(self) -> bool:
        return len(getattr(self, "_rows", [])) > self.ROWS

    def scroll(self, delta: int) -> None:
        if self.overflow():
            self._off = getattr(self, "_off", 0) + delta
            self._render()

    def _render(self) -> None:
        rows = getattr(self, "_rows", [])
        if len(rows) <= self.ROWS:
            self.overlay.set_card_text("\n".join(rows))
            return
        body = self.ROWS - 1
        off = max(0, min(getattr(self, "_off", 0), len(rows) - body))
        # keep a ▶ cursor row (picker / menu) in view
        cur = next((i for i, r in enumerate(rows) if r.lstrip().startswith("▶")), None)
        if cur is not None and not (off <= cur < off + body):
            off = max(0, min(cur - body // 2, len(rows) - body))
        self._off = off
        mark = ("▲" if off > 0 else " ") + ("▼" if off + body < len(rows) else " ")
        self.overlay.set_card_text(
            "\n".join(rows[off:off + body] + [f"{mark} {off + 1}-{min(off + body, len(rows))}/{len(rows)}  ↑↓ PgUp/Dn"]))

    def buttons(self, labels: dict, callback) -> None:
        self.overlay.add_approval_buttons(labels=labels, callback=callback)

    def close(self) -> None:
        self._release_keyboard()
        if self._key_id is not None:
            try:
                self.overlay._win.disconnect(self._key_id)
                self.overlay._win.disconnect(self._wheel_id)
            except Exception:
                pass
            self._key_id = None
        if self._opened:
            self.overlay.park_idle()
            self._opened = False


def _run_loop(card: Card, tick, tick_ms: int, deadline: float) -> None:
    """Run a GLib loop; `tick()` returns False to stop. Used by every mode."""
    GLib = card.GLib
    loop = GLib.MainLoop()
    card._loop = loop  # type: ignore[attr-defined]

    def _tick() -> bool:
        if time.time() > deadline:
            loop.quit()
            return False
        keep = tick()
        if not keep:
            loop.quit()
        return keep

    GLib.timeout_add(tick_ms, _tick)
    loop.run()


def _stop(card: Card) -> None:
    lp = getattr(card, "_loop", None)
    if lp is not None and lp.is_running():
        lp.quit()


# --------------------------------------------------------------------------
# modes
# --------------------------------------------------------------------------
def mode_picker(card: Card, pane: str, pk: dict) -> dict:
    trail: list[str] = []
    state = {"pk": pk, "result": None}
    deadline = time.time() + OVERALL_TIMEOUT_S

    def finish(action: str, reason: str = "") -> None:
        state["result"] = (action, reason)
        _stop(card)

    def nav(key: str) -> None:
        before = state["pk"]
        sb._tmux("send-keys", "-t", pane, key)
        new = wait_for_change(pane, before, timeout=3.0)
        if new is not None:
            state["pk"] = new
            card.text(render_picker(new, trail))

    def on_button(k: str) -> None:
        if k == "up":
            nav("Up")
        elif k == "down":
            nav("Down")
        elif k == "select":
            finish("select")
        else:
            finish("cancelled", "cancelled on the card")

    def on_key(name: str) -> bool:
        if name in ("Up", "k"):
            nav("Up")
        elif name in ("Down", "j"):
            nav("Down")
        elif name in ("Return", "KP_Enter"):
            finish("select")
        elif name == "Escape":
            finish("cancelled", "cancelled with Esc")
        else:
            return False
        return True

    card.on_key = on_key
    labels = {"up": "▲ Up", "down": "▼ Down", "select": "✔ Select", "cancel": "✖ Cancel"}

    def tick() -> bool:  # follow Sensei if it moves on its own
        if state["result"]:
            return False
        new = parse_picker(capture_color(pane))
        if new is not None and (new["cursor"], new["title"], new["items"]) != (
                state["pk"]["cursor"], state["pk"]["title"], state["pk"]["items"]):
            state["pk"] = new
            card.text(render_picker(new, trail))
        return True

    for _level in range(MAX_LEVELS):
        state["result"] = None
        card.buttons(labels, on_button)
        card.text(render_picker(state["pk"], trail))
        _run_loop(card, tick, 700, deadline)
        action, reason = state["result"] or ("cancelled", "timed out")
        if action == "cancelled":
            for _ in range(MAX_LEVELS):  # Esc once per open level until the picker is gone
                sb._tmux("send-keys", "-t", pane, "Escape")
                time.sleep(1.2)
                if parse_picker(capture_color(pane)) is None:
                    break
            return {"ok": False, "action": "cancelled", "reason": reason}
        chosen = state["pk"]["items"][state["pk"]["cursor"]]
        trail.append(re.sub(r"\s{2,}.*$", "", chosen))  # drop the "4 models" suffix
        sb._tmux("send-keys", "-t", pane, "Enter")
        nxt = wait_for_change(pane, state["pk"], timeout=5.0)
        if nxt is None or (nxt["title"] == state["pk"]["title"] and nxt["items"] == state["pk"]["items"]):
            break  # picker closed — selection complete
        state["pk"] = nxt
    sb.wait_ready(pane, timeout=10)
    footer = _MODEL_FOOTER_RE.search(sb._capture(pane))
    return {"ok": True, "mode": "picker", "action": "selected", "path": trail,
            "model": footer.group(1) if footer else ""}


def menu_card_text(menu: dict, cursor: int = 0) -> str:
    """What a first-time user needs on the card: the choices, the exact words
    that run each one by voice, and how to pick."""
    lines = [f"{menu['title']} — pick one", ""]
    for i, (label, _send, phrase) in enumerate(menu["items"], 1):
        lines.append(f"{'▶' if i - 1 == cursor else ' '}{i} {label} - {phrase}"[:38])
    lines += ["", "Arrows + Enter, a number, or say it."]
    return "\n".join(lines)


def mode_menu(card: Card, menu: dict) -> str | None:
    """Button menu for a command with ready-made options. Touches nothing in
    Sensei; returns the chosen slash command, or None if cancelled / timed out."""
    items = menu["items"]
    state: dict = {"choice": None, "done": False, "cur": 0}
    deadline = time.time() + OVERALL_TIMEOUT_S

    def move(d: int) -> None:
        state["cur"] = (state["cur"] + d) % len(items)
        card.text(menu_card_text(menu, state["cur"]))

    def pick(i: int | None) -> None:
        state["choice"] = items[i][1] if i is not None else None
        state["done"] = True
        _stop(card)

    def on_button(k: str) -> None:
        pick(int(k) if k.isdigit() else None)

    def on_key(name: str) -> bool:
        if name == "Escape":
            pick(None)
        elif name in ("Up", "Left", "k"):
            move(-1)
        elif name in ("Down", "Right", "j"):
            move(1)
        elif name in ("Return", "KP_Enter", "space"):
            pick(state["cur"])
        elif len(name) == 1 and name.isdigit() and 1 <= int(name) <= len(items):
            pick(int(name) - 1)
        else:
            return False
        return True

    card.open(menu_card_text(menu))
    card.on_key = on_key
    labels = {str(i): f"{i + 1} {lbl}" for i, (lbl, _s, _p) in enumerate(items)}
    labels["cancel"] = "✖ Cancel"
    card.buttons(labels, on_button)
    speak("Pick one: " + ", ".join(lbl for lbl, _s, _p in items) + ".")
    _run_loop(card, lambda: not state["done"], 400, deadline)
    card.on_key = None
    return state["choice"]


def mode_guide(card: Card) -> dict:
    """Browsable 'what can I say' card. Pure local — never touches Sensei."""
    pages = sb.guide_pages()
    state = {"i": 0, "done": False}
    deadline = time.time() + OVERALL_TIMEOUT_S

    def show() -> None:
        card.text(f"{pages[state['i']]}\npage {state['i'] + 1} of {len(pages)}")

    def go(d: int) -> None:
        state["i"] = max(0, min(len(pages) - 1, state["i"] + d))
        show()

    def close() -> None:
        state["done"] = True
        _stop(card)

    def on_button(k: str) -> None:
        go(1) if k == "next" else go(-1) if k == "back" else close()

    def on_key(name: str) -> bool:
        if name in ("Up", "Down") and card.overflow():
            card.scroll(-1 if name == "Up" else 1)
        elif name in ("Right", "Down", "n", "space", "Return", "KP_Enter"):
            go(1)
        elif name in ("Left", "Up", "b"):
            go(-1)
        elif name in ("Escape", "q"):
            close()
        else:
            return False
        return True

    card.open(pages[0])
    card.on_key = on_key
    card.buttons({"back": "◀ Back", "next": "Next ▶", "close": "✖ Close"}, on_button)
    show()
    speak("Say slash and a command. For example slash compact, slash mode, slash plan, or slash models. "
          "Use Next to see every command.")
    _run_loop(card, lambda: not state["done"], 500, deadline)
    return {"ok": True, "mode": "guide", "action": "closed", "pages": len(pages)}


def mode_pager(card: Card, pane: str) -> dict:
    state = {"done": False, "pages": 1, "last": ""}
    deadline = time.time() + OVERALL_TIMEOUT_S

    def send_line(s: str) -> None:
        # Never type into a pager that has already closed: the keystroke would
        # reach the model as chat (seen 2026-11-03: stray "b"/"n" prompts).
        if sb.modal_state(sb.capture_visible(pane)) not in ("pager", "hub"):
            log(f"pager gone; NOT sending {s!r}")
            state["done"] = True
            _stop(card)
            return
        log(f"pager send {s!r}")
        sb._tmux("send-keys", "-t", pane, "-l", "--", s)
        if s not in ("n", "b"):
            # Sensei's pager reads ONE key for n/b (Enter would turn the page twice).
            # Anything longer (a number to pick) is read as a line and needs Enter.
            sb._tmux("send-keys", "-t", pane, "Enter")
        time.sleep(1.2)
        refresh()

    def refresh() -> None:
        scr = sb.capture_visible(pane)   # visible screen only: scrollback is old shell text
        if sb.modal_state(scr) is None:
            state["done"] = True
            _stop(card)
            return
        t = pager_text(scr)
        if t != state["last"]:
            state["last"] = t
            card.text(t + "\n\n◀ ▶ pages · digits pick")

    def on_button(k: str) -> None:
        if k == "next":
            state["pages"] += 1
            send_line("n")
        elif k == "back":
            send_line("b")
        else:
            state["done"] = True
            _stop(card)

    def on_key(name: str) -> bool:
        if name in ("Up", "Down") and card.overflow():
            card.scroll(-1 if name == "Up" else 1)
        elif name in ("Right", "Down", "n", "Return", "KP_Enter", "space"):
            on_button("next")
        elif name in ("Left", "Up", "b"):
            on_button("back")
        elif name in ("Escape", "q"):
            on_button("close")
        elif len(name) == 1 and name.isdigit():
            send_line(name)
        else:
            return False
        return True

    card.on_key = on_key
    card.buttons({"back": "◀ Back", "next": "Next ▶", "close": "✖ Close"}, on_button)
    refresh()
    _run_loop(card, lambda: (refresh() or True) and not state["done"], 900, deadline)
    return {"ok": True, "mode": "pager", "action": "closed", "pages": state["pages"]}


def mode_output(card: Card, pane: str, send: str, output: str) -> dict:
    lines = [l for l in output.splitlines() if l.strip()]
    shown = lines[:OUTPUT_CARD_LINES]
    more = len(lines) - len(shown)
    body = "\n".join(shown) + (f"\n… {more} more lines — full output is in your Tahuti window" if more > 0 else "")
    digest = sb.spoken_digest(output, send.lstrip("/"))
    state = {"done": False, "spoke": False, "last_speech": 0.0}
    t0 = time.time()
    deadline = t0 + OVERALL_TIMEOUT_S

    card.text(f"{send}\n\n{body or '(no output)'}")

    def on_button(_k: str) -> None:
        state["done"] = True
        _stop(card)

    def out_key(name: str) -> bool:
        if name in ("Up", "Down") and card.overflow():
            card.scroll(-1 if name == "Up" else 1)
            return True
        if name in ("Escape", "Return", "KP_Enter"):
            on_button("done")
            return True
        return False

    card.on_key = out_key
    card.buttons({"done": "✔ Done"}, on_button)
    spoken = speak(digest)

    def tick() -> bool:
        if state["done"]:
            return False
        now = time.time()
        if tts_playing():
            state["spoke"] = True
            state["last_speech"] = now
            return True
        if state["spoke"]:                      # voice finished: leave ~1s later
            return now - state["last_speech"] < 1.0
        return now - t0 < NO_SPEECH_GRACE_S      # never started (muted/suppressed): short grace

    _run_loop(card, tick, 500, deadline)
    return {"ok": True, "mode": "output", "action": "shown", "output": output,
            "digest": digest, "spoken": bool(spoken and state["spoke"])}


# --------------------------------------------------------------------------
def main() -> int:
    send = "/model"
    if "--send" in sys.argv:
        send = sys.argv[sys.argv.index("--send") + 1]
    if not os.environ.get("DISPLAY"):
        os.environ["DISPLAY"] = ":0"
    try:
        card = Card()
    except Exception as exc:
        return _out({"ok": False, "action": "unavailable", "reason": f"overlay not available: {exc}"})

    if "--guide" in sys.argv:
        try:
            return _out(mode_guide(card))
        finally:
            card.close()
    chosen_from_menu = ""
    if "--menu" in sys.argv:
        menu = sb.menu_for(sys.argv[sys.argv.index("--menu") + 1])
        if menu is None:
            return _out({"ok": False, "action": "error", "reason": "no menu for that command"})
        try:
            choice = mode_menu(card, menu)
        finally:
            card.close()
        if not choice:
            return _out({"ok": False, "action": "cancelled", "reason": "cancelled on the menu"})
        send = chosen_from_menu = choice
        log(f"MENU chose {choice!r}")
        card = Card()   # fresh card for the command's own result

    pane, _started = sb.ensure_sensei()
    if not pane:
        return _out({"ok": False, "action": "unavailable", "reason": "Sensei is not running and could not be started"})
    sb.wait_ready(pane, timeout=30)
    if not sb.ensure_idle(pane):
        return _out({"ok": False, "action": "error", "reason": "Sensei is in a menu I couldn't close"})

    log(f"START send={send!r}")
    before = sb.capture_visible(pane)
    sb._tmux("send-keys", "-t", pane, "-l", "--", send)
    sb._tmux("send-keys", "-t", pane, "Enter")
    after = sb._wait_settled(pane, settle=5.0, timeout=30, min_wait=5.0,
                             stop=lambda scr: sb.modal_state(scr) is not None)
    state = sb.modal_state(after)
    after_vis = sb.capture_visible(pane)
    try:
        if state == "picker":
            pk = parse_picker(capture_color(pane))
            if pk is not None:
                card.open(render_picker(pk, []))
                log("CARD_OPEN picker")
                return _out(mode_picker(card, pane, pk))
        if state == "prompt":
            # A question is waiting in Sensei. Typing anything would answer it,
            # so type nothing: show it and let the operator answer in Sensei.
            txt = "\n".join(sb._live_body(after_vis)[-8:])
            card.open(f"{send}\n\n…")
            log("CARD_OPEN prompt (nothing typed)")
            return _out(mode_output(card, pane, send,
                                    "Sensei is asking you a question. Answer it in the Sensei window.\n" + txt))
        if state in ("pager", "hub"):
            card.open(pager_text(after_vis))
            log("CARD_OPEN pager")
            return _out(mode_pager(card, pane))
        output = sb.extract_output(before, after_vis, send)
        card.open(f"{send}\n\n…")
        log("CARD_OPEN output")
        return _out(mode_output(card, pane, send, output))
    except Exception as exc:  # fail closed: leave Sensei out of any menu
        return _out({"ok": False, "action": "error", "reason": f"{type(exc).__name__}: {exc}"})
    finally:
        card.close()
        try:
            sb.ensure_idle(pane)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
