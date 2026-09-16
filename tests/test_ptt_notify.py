"""Regression test for the PTT hot-path / Hermex notify bug (2026-09-15).

commit 7ca6144 added synchronous urlopen(timeout=10) calls to what is now
ptt_notify.notify_hermex(), called from ptt_pynput.stop_and_send() INSIDE
the critical section it holds _processing_lock for -- start_recording()
blocks on that same lock with no timeout, so a slow/hung hermes-webui
could stall the next F13 press up to ~20-30s. Fixed by backgrounding the
call on a daemon thread and extracting it into its own module (ptt_pynput
itself has import-time side effects -- a singleton file lock, background
listener threads, a blocking listener.join() -- that make it unsafe to
import in a test process).

This test executes the real function against a stalling fake HTTP call
and asserts it returns near-instantly regardless -- it must fail if this
ever regresses back to a blocking call.
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

os.environ.setdefault("HERMES_SESSION_ID", "test-session")

import ptt_notify  # noqa: E402


def test_notify_hermex_does_not_block_on_slow_server(monkeypatch):
    """The actual regression: a hung notify target must not delay the caller."""

    def _slow_impl(*args, **kwargs):
        time.sleep(2)  # stands in for a hung hermes-webui
        raise TimeoutError("simulated hang")

    monkeypatch.setattr(ptt_notify, "_notify_hermex_impl", _slow_impl)

    start = time.monotonic()
    ptt_notify.notify_hermex("test title", "test body")
    elapsed = time.monotonic() - start

    assert elapsed < 0.5, (
        f"notify_hermex() blocked the caller for {elapsed:.2f}s -- it must "
        "fire the HTTP call on a background thread and never block the "
        "PTT hot path (start_recording()/stop_and_send() share a lock "
        "this function must never hold)."
    )


def test_notify_hermex_calls_shared_client_with_correct_args(monkeypatch):
    """Guards against re-drift into a second, duplicated implementation."""
    calls = []

    def _fake_impl(title, body, urgency="normal", sound=False):
        calls.append((title, body, urgency, sound))
        return {"ok": True, "delivered": 0}

    monkeypatch.setattr(ptt_notify, "_notify_hermex_impl", _fake_impl)

    ptt_notify.notify_hermex("hello", "world", urgency="high", sound=True)
    time.sleep(0.2)  # let the daemon thread run

    assert calls == [("hello", "world", "high", True)]


def test_notify_hermex_swallows_exceptions():
    """A notify failure must never propagate to the (recording/sending) caller."""

    ptt_notify.notify_hermex("", "")  # empty title+body -> ValueError inside the real client
    time.sleep(0.2)  # if this raised, pytest would already have failed above


def test_sanitize_for_notify_strips_home_directory():
    """The exception text sent to a phone push notification must not leak
    the local home directory path -- exercises the real function used by
    ptt_pynput.stop_and_send()'s error path, not a reimplementation."""
    home = os.path.expanduser("~")
    fake_exc_text = f"could not read {home}/secret/transcript.txt"

    sanitized = ptt_notify.sanitize_for_notify(fake_exc_text)

    assert home not in sanitized
    assert sanitized == "could not read ~/secret/transcript.txt"


def test_sanitize_for_notify_redacts_before_truncating():
    """Regression: truncate-then-replace can cut a home path in half, so it
    no longer matches and a partial path leaks through instead of being
    redacted. Build a string where the home directory straddles the
    max_len cutoff and confirm no fragment of it survives."""
    home = os.path.expanduser("~")
    padding = "x" * (100 - len(home) // 2)
    text = padding + home + "/secret.txt"

    sanitized = ptt_notify.sanitize_for_notify(text, max_len=120)

    assert home not in sanitized
    # No trailing fragment of the home path (e.g. its last few characters)
    # should appear in the tail of the sanitized, truncated string.
    assert home[-5:] not in sanitized
