#!/usr/bin/env python3
"""send_to_chat.py — message one of the open agent chats AND make sure it was submitted.

`aoe send` types the message into the chat's input box; Hermes in particular can leave it
sitting there unsent (Lijah found two chats idle with my messages unsent, 2026-11-04).
This sends, waits, looks at the pane, and presses Enter if the text is still in the box
and the chat is not busy.

    python3 send_to_chat.py <aoe-id-or-title> "message"
"""

from __future__ import annotations

import re
import subprocess
import sys
import time

BUSY = re.compile(
    r"esc interrupt|ctrl\+c cancel|pondering|thinking|brainstorming|waiting on|"
    r"msg=interrupt|type to redirect",
    re.I,
)
# Round-5 finding #5: bare "running" matched the USER's own pending text
# ("running out of time, remind me later") sitting in the input box, so the
# submit-Enter never fired. An agent's ACTIVITY line is a status-drawing
# special char or marker followed by Running — prose in the middle of a line
# is not busy.
RUNNING_LINE = re.compile(r"^[\s●•◆✦✧✱✳✻❋*]+running\b", re.I)


def tmux_session_for(sid: str) -> str | None:
    out = subprocess.run(
        ["tmux", "ls", "-F", "#{session_name}"], capture_output=True, text=True
    ).stdout
    return next((n for n in out.splitlines() if n.endswith(sid[:8])), None)


def pane_tail(session: str, n: int = 8) -> list[str]:
    txt = subprocess.run(
        ["tmux", "capture-pane", "-t", session, "-p"], capture_output=True, text=True
    ).stdout
    return [ln for ln in txt.splitlines() if ln.strip()][-n:]


def needs_enter(tail: list[str], message: str) -> bool:
    """My text is still in the input box and nothing says the chat is working on it."""
    probe = re.sub(r"\s+", " ", message)[:30]
    flat = [re.sub(r"\s+", " ", ln) for ln in tail]
    still_there = any(probe in ln for ln in flat[-4:])
    # Round-5 finding #5: lines that are the USER's own pending message (the
    # probe was typed into the box and echoed back) are never busy evidence —
    # "running" INSIDE my own text must not block the submit.
    busy = any(
        BUSY.search(ln) or RUNNING_LINE.search(ln.strip())
        for ln in flat
        if not (probe and probe in ln)
    )
    return still_there and not busy


def send(sid: str, message: str, wait_s: float = 4.0) -> str:
    r = subprocess.run(["aoe", "send", sid, message], capture_output=True, text=True)
    if r.returncode != 0:
        return f"send failed rc={r.returncode}: {(r.stderr or r.stdout).strip()[:120]}"
    session = tmux_session_for(sid)
    if not session:
        return "sent (could not find its tmux session to verify)"
    time.sleep(wait_s)
    if needs_enter(pane_tail(session), message):
        subprocess.run(["tmux", "send-keys", "-t", session, "Enter"])
        time.sleep(2)
        return "sent; message was sitting in the input box — pressed Enter"
    return "sent and submitted"


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    print(send(sys.argv[1], " ".join(sys.argv[2:])))
