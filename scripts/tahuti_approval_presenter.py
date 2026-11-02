#!/usr/bin/env python3
"""One-box approval presenter for the Tahuti goal runner.

The goal runner is a headless daemon: it cannot own a GTK window. When a
goal escalates, the runner spawns THIS script — a thin transport around
``tahuti_avatar.prompt()``, the same in-process call the desktop gate's
callback makes (see tahuti_desktop._install_tahuti_callback). The card
that appears is Tahuti's one box from THIS repository. Until 2026-11-02
the runner instead shelled out to master-ai-cli's ninja overlay
(~/master-ai-cli/ninja/avatar_approval.py), a foreign product's surface
whose waits timed out at 150s and whose verdicts never arrived.

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
