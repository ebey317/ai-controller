#!/usr/bin/env python3
"""handoff_watch.py — the hourly "is anyone actually doing the task?" check.

Operator's rule (2026-11-04): tell every session ONCE to work from the handoff doc's
TASK BOARD and look for more tasks when they finish. Only when a task's owner has NOT
started it do I assign it directly (a message through `aoe send`).

    python3 handoff_watch.py            # check; message only owners who have not started
    python3 handoff_watch.py --dry-run  # say what it WOULD send; send nothing
    python3 handoff_watch.py --report   # print the board and who is doing what; send nothing

The board lives in ~/MD/HANDOFF.md between these markers, one task per table row:

    <!-- TASKBOARD:BEGIN -->
    | id | task | owner | status | evidence |
    |---|---|---|---|---|
    | T1 | Run no-mistakes + OCR on both branches | gpt model. | TODO | |
    <!-- TASKBOARD:END -->

status: TODO | DOING | DONE | BLOCKED.  owner: an aoe session title (or id), "ANY", or "Lijah".
Deterministic on purpose: no model, no tokens, safe to run every hour overnight.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

HANDOFF = Path.home() / "MD" / "HANDOFF.md"
STATE = Path.home() / ".tahuti" / "handoff_watch_state.json"
LOG = Path.home() / ".tahuti" / "handoff_watch.log"
BEGIN, END = "<!-- TASKBOARD:BEGIN -->", "<!-- TASKBOARD:END -->"
RESEND_AFTER_S = 55 * 60      # at most one direct assignment per task per hour
# Round-3 review #1: make the active window EXPLICIT. The watch lives
# 19:00–23:59 and 00:00–03:59; outside that (04:00–18:59) it is a no-op that
# just logs and exits 0.
WINDOW_START_H = 19           # 19:00
WINDOW_END_H = 4              # through 03:59


def in_watch_window(hour: int, minute: int = 0) -> bool:
    return (WINDOW_START_H <= hour <= 23) or (0 <= hour < WINDOW_END_H)


def parse_board(text: str) -> list[dict]:
    if BEGIN not in text or END not in text:
        return []
    block = text.split(BEGIN, 1)[1].split(END, 1)[0]
    rows = []
    for line in block.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or cells[0].lower() in ("id", "") or set(cells[0]) <= set("-: "):
            continue
        rows.append({"id": cells[0], "task": cells[1], "owner": cells[2],
                     "status": cells[3].upper(), "evidence": cells[4] if len(cells) > 4 else ""})
    return rows


def sessions() -> dict[str, dict]:
    """aoe sessions that are alive: {id: {title, state}} from `aoe ps` + `aoe list`."""
    try:
        ps = subprocess.run(["aoe", "ps"], capture_output=True, text=True).stdout.splitlines()
    except (OSError, FileNotFoundError) as e:   # aoe not on PATH (timer env, etc.)
        log(f"cannot run `aoe ps` ({e.__class__.__name__}) — treating as no sessions")
        return {}
    out: dict[str, dict] = {}
    for ln in ps:
        m = re.match(r"^([0-9a-f]{8})\s+(.+?)\s{2,}\S+\s+(idle|running|waiting)\b", ln)
        if m:
            out[m.group(1)] = {"title": m.group(2).strip(), "state": m.group(3)}
    return out


def find_session(owner: str, live: dict[str, dict]) -> tuple[str, dict] | None:
    o = owner.lower().strip()
    for sid, info in live.items():
        t = info["title"].lower().rstrip(". ")
        # Round-3 review #2c: a blank/ellipsis-only title must never match —
        # "no title yet" is not evidence for any owner.
        if len(t) < 4:
            continue
        if sid in o.split() or o == sid or o == t or (len(o) > 6 and (o.startswith(t.rstrip("…. ")) or t.startswith(o[:12]))):
            return sid, info
    return None


def send_message(sid: str, msg: str) -> tuple[bool, str]:
    """Send ONE message and make sure it was actually submitted.

    Round-3 review #2a/#10: bare `aoe send` can leave the message sitting in
    the chat's input box (Hermes especially). scripts/send_to_chat.send()
    waits, reads the pane, and presses Enter if the text is still sitting
    there and the chat is not busy. Returns (ok, detail); the resend time is
    ONLY recorded by the caller when ok is True."""
    try:
        from send_to_chat import send as chat_send  # same scripts/ dir
        detail = chat_send(sid, msg)
    except ImportError:
        # Module layout that doesn't expose it: fall back to bare send with
        # the same submission check semantics unavailable.
        try:
            r = subprocess.run(["aoe", "send", sid, msg], capture_output=True, text=True)
        except (OSError, FileNotFoundError) as e:
            return False, f"send failed: {e.__class__.__name__}"
        detail = ("sent (bare aoe send; submission not verified)"
                  if r.returncode == 0
                  else f"send failed rc={r.returncode}: {(r.stderr or r.stdout).strip()[:120]}")
    except (OSError, FileNotFoundError) as e:
        return False, f"send failed: {e.__class__.__name__}"
    return not detail.startswith("send failed"), detail


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)


def is_busy(info: dict) -> bool:
    """Should the watch leave this session strictly alone?

    Round-3 review #2d: `waiting` counts as busy — a session blocked on an
    approval prompt gets its prompt ANSWERED if anything types into it.

    (This replaces the old `state == "running"` check, which left a session
    sitting on a card under constant STM interrupts.)"""
    return info.get("state") in ("running", "waiting")


def assignment(task: dict) -> str:
    return (f"HANDOFF TASK {task['id']} is assigned to you and still shows TODO: {task['task']}. "
            f"Read the TASK BOARD in ~/MD/HANDOFF.md, set your row to DOING when you start and "
            f"DONE with evidence when you finish, then look at the board for more. "
            f"Do not push or merge without Lijah's approval.")


def main(argv: list[str]) -> int:
    dry, report = "--dry-run" in argv, "--report" in argv
    lt = time.localtime()
    if not report and not in_watch_window(lt.tm_hour, lt.tm_min):
        # Round-3 review #1: explicit window. 04:00–18:59 the watch is off —
        # the old code kept messaging sessions between 04:00 and 04:06-plus.
        log("outside watch window (19:00–23:59, 00:00–03:59) — nothing to do")
        return 0
    board = parse_board(HANDOFF.read_text(encoding="utf-8")) if HANDOFF.exists() else []
    if not board:
        log("no TASKBOARD found in HANDOFF.md")
        return 1
    live, state, now = sessions(), load_state(), time.time()
    counts = {s: sum(1 for t in board if t["status"] == s) for s in ("TODO", "DOING", "DONE", "BLOCKED")}
    log(f"board: {counts}  sessions: " + ", ".join(f"{i['title'][:18]}={i['state']}" for i in live.values()))
    sent = 0
    for t in board:
        if t["status"] != "TODO" or t["owner"].lower() in ("any", "lijah", ""):
            continue
        found = find_session(t["owner"], live)
        if not found:
            log(f"{t['id']}: owner '{t['owner']}' has no live chat — cannot assign")
            continue
        sid, info = found
        if is_busy(info):
            log(f"{t['id']}: {info['title'][:20]} is {info['state']} — leave it")
            continue
        if now - state.get(t["id"], 0) < RESEND_AFTER_S:
            log(f"{t['id']}: assigned within the last hour already")
            continue
        msg = assignment(t)
        if report or dry:
            log(f"{t['id']}: WOULD send to {info['title'][:20]} ({sid}): {msg[:90]}…")
            continue
        ok, detail = send_message(sid, msg)
        log(f"{t['id']}: {'sent' if ok else 'NOT sent'} to {info['title'][:20]} "
            f"({sid}): {detail}  (enter-needed: {'yes' in detail or 'Enter' in detail})")
        if not ok:
            # Round-3 review #2a: only a SUCCESSFUL send may start the hourly
            # resend quiet-period; a failed send is retried next hour.
            continue
        state[t["id"]] = now
        sent += 1
    if sent and not dry and not report:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
