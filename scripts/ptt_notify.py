"""Non-blocking Hermex notify wrapper for ptt_pynput.py's hot path.

Extracted 2026-09-15 as its own module specifically so it can be imported
and tested in isolation. ptt_pynput.py itself runs real side effects at
import time (a singleton file lock, background listener threads, a
blocking `keyboard.Listener(...).join()`) and can't be safely imported by
a test process -- this module has none of that, only the notify wrapper.

Why this exists at all: commit 7ca6144 added synchronous
urlopen(timeout=10) calls to Hermex's /api/notify directly inside
ptt_pynput.stop_and_send()'s critical section -- the same
threading.Lock (_processing_lock, no timeout on either side) that
start_recording() blocks on before it can open the mic for the next F13
press. A slow or hung (not just down) hermes-webui could stall the next
press up to ~20-30s. See tests/test_ptt_notify.py for the regression test
that must keep passing.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

# Make notify_hermex importable regardless of what the caller already did to
# sys.path -- ptt_pynput.py and tests/test_ptt_notify.py both happen to
# prepend scripts/ themselves today, but nothing enforces that for a future
# caller (a cron job, a second CLI wrapper) that imports this module directly.
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from notify_hermex import notify_hermex as _notify_hermex_impl

log = logging.getLogger(__name__)


def sanitize_for_notify(text: str, max_len: int = 120) -> str:
    """Strip the local home directory before text leaves the machine in a
    push notification body (exception text, transcript fragments, ...).

    Replace-then-truncate, not the reverse: truncating first can cut a home
    path in half, and the home-dir string then no longer matches, leaking
    the partial path instead of redacting it.
    """
    return text.replace(os.path.expanduser("~"), "~")[:max_len]


def notify_hermex(title: str, body: str, urgency: str = "normal", sound: bool = False) -> None:
    """Fire-and-forget push notification to the Hermex iOS app.

    Runs on a daemon thread so a slow or unreachable hermes-webui can never
    delay the dictation pipeline -- only network/HTTP exceptions are
    swallowed (logged at debug level); this function itself never blocks
    the caller at all, which is the actual guarantee the PTT hot path
    needs. Do not make this call synchronous again -- see module docstring.
    """
    def _fire():
        try:
            _notify_hermex_impl(title, body, urgency=urgency, sound=sound)
        except Exception:
            log.debug("Hermex notify failed", exc_info=True)

    threading.Thread(target=_fire, daemon=True).start()
