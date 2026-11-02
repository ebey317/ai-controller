"""Attended-path proofs for the runner's one-box approval (2026-11-02).

The unit tests in test_tahuti_goal_runner.py prove the gate logic and
the fail-fast transport with stub presenters. These tests prove the
other half on a private Broadway display (never the operator's :0):

- a simulated operator CAN approve through Tahuti's one box and the
  runner's approved write actually executes (runner_approval_driver.py);
- the real presenter subprocess prints READY only when the box is up,
  and an unanswered card comes back as the box's own timeout "deny".
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DRIVER = REPO / "tests" / "runner_approval_driver.py"
PRESENTER = REPO / "scripts" / "tahuti_approval_presenter.py"


def _free_broadway_display() -> int | None:
    for display in range(7, 12):
        port = 8080 + display
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return display
    return None


class _Broadway:
    """Context manager: a private broadwayd + the env to reach it."""

    def __enter__(self):
        if shutil.which("broadwayd") is None:
            pytest.skip("broadwayd not installed")
        display = _free_broadway_display()
        if display is None:
            pytest.skip("no free Broadway display")
        self.display = display
        self.server = subprocess.Popen(
            ["broadwayd", f":{display}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port = 8080 + display
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.2)
        else:
            self.server.terminate()
            pytest.skip("broadwayd did not start")
        self.env = {
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(Path.home()),
            "XDG_RUNTIME_DIR": os.environ.get(
                "XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"),
            "GDK_BACKEND": "broadway",
            "BROADWAY_DISPLAY": f":{display}",
            "DISPLAY": f":{display}",
            "PYTHONPATH": str(REPO),
        }
        return self

    def __exit__(self, *exc):
        self.server.terminate()
        try:
            self.server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.server.kill()
        return False


def test_runner_approval_through_one_box():
    with _Broadway() as bw:
        run = subprocess.run(
            [sys.executable, str(DRIVER)],
            cwd=str(REPO), env=bw.env, capture_output=True, text=True,
            timeout=180)
        if run.returncode != 0:
            pytest.fail(f"runner approval driver failed:\n{run.stdout}\n{run.stderr}")
        results = json.loads(run.stdout.strip().splitlines()[-1])

    approved = results["approved"]
    assert approved["result"]["ok"] is True
    assert approved["result"]["status"] == "executed"
    assert approved["exists"] is True
    assert approved["content"] == "hello from the proof"

    denied = results["denied"]
    assert denied["result"]["ok"] is False
    assert denied["exists"] is False


def test_presenter_subprocess_ready_and_unanswered_deny():
    with _Broadway() as bw:
        t0 = time.monotonic()
        run = subprocess.run(
            [sys.executable, str(PRESENTER)],
            input=json.dumps({
                "pattern_key": "tahuti:write_file:background",
                "description": "Write a proof note",
                "extra": {"why": "presenter protocol proof"},
                "timeout_seconds": 2,
            }),
            cwd=str(REPO), env=bw.env, capture_output=True, text=True,
            timeout=120)
        elapsed = time.monotonic() - t0
    assert run.returncode == 0, run.stderr
    lines = [ln.strip() for ln in run.stdout.splitlines() if ln.strip()]
    assert lines[0] == "READY"          # the box really was presented
    assert lines[-1] == "deny"          # unanswered -> the box's timeout deny
    assert 1.5 <= elapsed < 30          # the 2s answer window, not a hang
