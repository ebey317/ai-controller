#!/usr/bin/env python3
"""Broadway driver for the runner's approval path (not a pytest module).

Runs inside a private Broadway GTK session, like tahuti_box_driver.py,
and proves the ATTENDED path end to end: a simulated operator answers
in Tahuti's one box and the runner's gated write actually executes;
a Deny leaves no file. Only the presenter TRANSPORT is stubbed — an
in-process call to tahuti_avatar.prompt(), the exact call the
presenter subprocess makes (the subprocess wire protocol itself is
covered by test_presenter_subprocess_ready_and_unanswered_deny in
test_runner_approval_broadway.py and by the runner unit tests). The
box, the runner's per-goal escalation cache, and the write execution
are all the real code.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from scripts import tahuti_avatar as av  # noqa: E402
from scripts import tahuti_goal_runner as runner  # noqa: E402


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


def main() -> int:
    results: dict = {}
    Gtk.init([])

    sandbox = Path(tempfile.mkdtemp(prefix="tahuti-approval-proof-"))
    runner.APPROVALS_DIR = sandbox / "approvals"
    runner.GRANTS_FILE = sandbox / "grants.json"
    runner._is_under_home = lambda p: True
    runner._run_presenter = lambda pattern, description, extra: av.prompt(
        pattern, description, **extra
    )

    # Approved in the box -> the write executes for real.
    target = sandbox / "note.txt"
    _after(600, lambda: _buttons()["Yes"].clicked())
    t0 = time.monotonic()
    res = runner._write_file_tool(
        "proof-goal-yes", str(target), "hello from the proof", "broadway driver proof"
    )
    results["approved"] = {
        "result": res,
        "exists": target.exists(),
        "content": target.read_text(encoding="utf-8") if target.exists() else None,
        "elapsed": round(time.monotonic() - t0, 2),
    }

    # Denied in the box -> nothing is written.
    target2 = sandbox / "note2.txt"
    _after(600, lambda: _buttons()["Deny"].clicked())
    res2 = runner._write_file_tool(
        "proof-goal-deny", str(target2), "must not land", "broadway driver proof"
    )
    results["denied"] = {"result": res2, "exists": target2.exists()}

    av._reset_box()
    print(json.dumps(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
