#!/usr/bin/env python3
"""One-box approval presenter for the Tahuti goal runner.

The goal runner is a headless daemon: it cannot own a GTK window. When a
goal escalates, the runner spawns THIS script — a thin transport that
prefers master-ai-cli's real ThootieOverlay (the operator's actual
floating avatar; the card spawns FROM the avatar, same window -- "the
Tahuti run card"), called IN-PROCESS via
``ninja.avatar_approval.ask_avatar_approval()``, the same in-process call
tahuti_desktop._install_tahuti_callback makes for the cua_* desktop gate.
Falls back to this repo's own standalone ``tahuti_avatar.prompt()`` box if
master-ai-cli/ninja isn't importable here.

Until 2026-11-02 the runner shelled out to that same ninja overlay via
``subprocess.Popen(...).communicate()``, which hung 150s per approval and
whose verdicts never arrived -- that was first "fixed" by dropping the
real floating avatar entirely for the standalone box below. Re-verified
live 2026-11-02: the hang was the subprocess call itself (lifecycle/pipe
overhead), not the overlay -- an in-process import resolves in seconds,
so the real floating avatar is back as the preferred surface.

Wire protocol (stdout, line-oriented):
  stdin:  one JSON object {"pattern_key": str, "description": str,
          "extra": {...}} — "extra" is forwarded to prompt() as card
          fields (why/how/...). Optional "timeout_seconds" overrides the
          box's answer window (tests only; the runner never sends it).
  stdout line 1: "READY" once the card is about to be presented, or
          "UNAVAILABLE <reason>" when no card can be shown at all
          (no display, no GTK, approval box disabled in settings).
  stdout line 2: the verdict — "once" | "session" | "deny" — printed
          only after READY. The box itself resolves an unanswered card
          to "deny" at its timeout, so a verdict always means the card
          really was on screen.
  exit code: 0 when a verdict was produced, 2 when UNAVAILABLE.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _unavailable(reason: str) -> int:
    print(f"UNAVAILABLE {reason}", flush=True)
    return 2


def main() -> int:
    try:
        request = json.loads(sys.stdin.read() or "{}")
    except Exception as exc:
        return _unavailable(f"bad request: {exc}")
    pattern_key = str(request.get("pattern_key") or "")
    description = str(request.get("description") or "")
    extra = request.get("extra") or {}
    if not isinstance(extra, dict):
        extra = {}
    if not pattern_key or not description:
        return _unavailable("pattern_key and description are required")

    try:
        import tahuti_avatar as av
    except Exception as exc:
        return _unavailable(f"avatar surface failed to load: {exc}")
    if not av.GTK_AVAILABLE:
        return _unavailable("GTK is not available in this process")
    if not os.environ.get("DISPLAY"):
        return _unavailable("no display")
    try:
        settings = av.get_avatar_settings(av.load_profiles())
    except Exception:
        settings = {}
    if not settings.get("approval_box", True) or not settings.get("popup_on_approval", True):
        return _unavailable("the approval box is turned off in avatar settings")

    timeout = request.get("timeout_seconds")
    if isinstance(timeout, (int, float)) and timeout > 0:
        av.TIMEOUT_SECONDS = int(timeout)

    overlay = None
    try:
        ninja_root = os.path.expanduser("~/master-ai-cli")
        if ninja_root not in sys.path:
            sys.path.insert(0, ninja_root)
        from ninja.state_machine import get_overlay

        overlay = get_overlay(agent="tahuti")
    except Exception:
        overlay = None

    if overlay is not None:
        # Call present_approval() directly, not ask_avatar_approval()'s
        # wrapper -- that wrapper hardcodes timeout=None for agent=="tahuti"
        # ("wait forever"), silently discarding the timeout_seconds this
        # request asked for. Found live 2026-11-02: a timeout_seconds=2
        # request hung indefinitely instead of denying after 2s.
        from ninja.card_box import render_card_text
        from ninja.state_machine import ThootieState

        card = av.card_details(pattern_key, description, extra)
        overlay.set_agent("tahuti")
        overlay.set_card_text(render_card_text(card))
        overlay.machine.set_state(
            ThootieState.APPROVAL_INCOMING,
            overlay.machine.payload if overlay.machine.payload else None,
        )
        if not overlay._win.get_visible():
            overlay.show()
        print("READY", flush=True)
        verdict = overlay.present_approval(timeout=av.TIMEOUT_SECONDS)
        overlay.park_idle()
        print(verdict, flush=True)
        return 0

    try:
        # Create the box now, hidden: if the display cannot actually be
        # opened, prompt() would swallow the failure and report a plain
        # "deny" for a card that never existed. Fail honestly instead.
        av._get_box()
    except Exception as exc:
        return _unavailable(f"the box could not be created: {exc}")
    print("READY", flush=True)
    verdict = av.prompt(pattern_key, description, **extra)
    print(verdict, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
