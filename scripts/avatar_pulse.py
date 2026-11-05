"""avatar_pulse.py — the ONE verified kill for the avatar pulse pid file.

Shared by tahuti_goal_runner.py and ptt_pynput.py (round-4 review #3: both
used to fire `os.kill(pid, 9)` off /tmp/tahuti-pulse.pid with no check; that
file lives in a world-writable directory and PIDs get recycled — a stale
entry could kill an innocent process). Kill only after positive evidence:

  * the process must run as THIS user (/proc/<pid> uid == os.getuid()), and
  * its /proc cmdline must name the avatar pulse entry script
    (avatar_approval.py) — i.e. it really is the pulse, not a recycled pid.

    from avatar_pulse import kill_avatar_pulse
    kill_avatar_pulse()             # default /tmp/tahuti-pulse.pid
    kill_avatar_pulse(pidfile=path) # tests / alternate pid files

Every function here is self-contained (no project imports) so both importers
stay side-effect free.
"""
from __future__ import annotations

import os
from pathlib import Path

PULSE_PIDFILE = "/tmp/tahuti-pulse.pid"


def pulse_cmdline_uid(pid: int) -> tuple[bytes, int] | None:
    """(cmdline, uid) of /proc/<pid>, or None when it vanished or is unread."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmd = f.read()
        uid = os.stat(f"/proc/{pid}").st_uid
    except (OSError, ValueError):
        return None
    return cmd, uid


def avatar_pid_is_safe(pid: int, cmdline: bytes, proc_uid: int) -> bool:
    """May THIS pid be SIGKILLed as the avatar pulse? This user's id AND the
    avatar entry script in its cmdline (round-4 finding #3)"""
    return proc_uid == os.getuid() and b"avatar_approval" in cmdline


def kill_avatar_pulse(pidfile: str = PULSE_PIDFILE) -> bool:
    """Retire the avatar pulse recorded in a pid file — after verifying it.
    Returns True only when a verified kill happened. Refuses: unreadable or
    unparsable pid files, pid <= 1, vanished or unreadable /proc entries,
    foreign uid processes, and any cmdline that is not the avatar pulse."""
    try:
        pid = int(Path(pidfile).read_text(encoding="utf-8").strip())
    except Exception:
        return False
    if pid <= 1:
        return False
    info = pulse_cmdline_uid(pid)
    if info is None:
        return False
    cmd, uid = info
    if not avatar_pid_is_safe(pid, cmd, uid):
        return False
    try:
        os.kill(pid, 9)
        return True
    except OSError:
        return False
