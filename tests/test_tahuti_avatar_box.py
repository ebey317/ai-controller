"""One-box law tests for Tahuti's avatar surface (Lijah, 2026-10-31).

The avatar and the approval query are THE SAME BOX: one top-level
window, the query presented inside it, Edit an in-box state — never a
second window, never a dialog. The GTK scenarios run against a private
Broadway display (broadwayd), never the operator's :0 desktop.
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

from scripts.tahuti_avatar import corner_position, corners_for

REPO = Path(__file__).resolve().parent.parent
AVATAR_SRC = REPO / "scripts" / "tahuti_avatar.py"
DRIVER = REPO / "tests" / "tahuti_box_driver.py"


# ── corner loop (pure) ────────────────────────────────────────────────


def test_corner_loop_never_uses_notification_corner():
    corners = corners_for({})
    assert "top-right" not in corners  # default notification corner
    assert corners == ["top-left", "bottom-left", "bottom-right"]


def test_corner_loop_respects_custom_notification_corner():
    corners = corners_for({"notification_corner": "bottom-left"})
    assert "bottom-left" not in corners
    assert "bottom-right" in corners


def test_corner_loop_ignores_notification_corner_when_not_respected():
    corners = corners_for(
        {"notification_corner": "bottom-left", "respect_notification_corner": False}
    )
    assert "bottom-left" in corners


def test_corner_position_math():
    assert corner_position("top-left", 200, 100, 1920, 1080) == (16, 16)
    assert corner_position("bottom-left", 200, 100, 1920, 1080) == (16, 964)
    assert corner_position("bottom-right", 200, 100, 1920, 1080) == (1704, 964)
    # clamped on-screen even when the box is oversized
    x, y = corner_position("bottom-right", 5000, 4000, 1920, 1080)
    assert x >= 16 and y >= 16


# ── source guard: the one-box law is structural ───────────────────────


def test_source_has_exactly_one_window_and_no_dialogs():
    source = AVATAR_SRC.read_text()
    assert source.count("Gtk.Window(") == 1
    assert "MessageDialog" not in source
    assert "Gtk.Dialog" not in source


# ── Broadway integration: the six gate scenarios in the one box ───────


def _free_broadway_display() -> int | None:
    for display in range(7, 12):
        port = 8080 + display
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return display
    return None


@pytest.mark.skipif(shutil.which("broadwayd") is None, reason="broadwayd not installed")
def test_gate_scenarios_in_one_box():
    display = _free_broadway_display()
    if display is None:
        pytest.skip("no free Broadway display")
    server = subprocess.Popen(
        ["broadwayd", f":{display}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        port = 8080 + display
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.2)
        else:
            pytest.skip("broadwayd did not start")
        env = {
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(Path.home()),
            # broadwayd keeps its socket in XDG_RUNTIME_DIR; the GTK
            # Broadway client cannot open the display without it.
            "XDG_RUNTIME_DIR": os.environ.get(
                "XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"
            ),
            "GDK_BACKEND": "broadway",
            "BROADWAY_DISPLAY": f":{display}",
            "DISPLAY": f":{display}",
            "PYTHONPATH": str(REPO),
        }
        run = subprocess.run(
            [sys.executable, str(DRIVER)],
            cwd=str(REPO),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if run.returncode != 0:
            pytest.fail(f"broadway driver failed:\n{run.stdout}\n{run.stderr}")
        results = json.loads(run.stdout.strip().splitlines()[-1])
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()

    assert results["deny"] == {"verdict": "deny", "toplevels": 1}
    assert results["yes"]["verdict"] == "once"
    assert results["yes"]["same_window"] is True
    assert results["always"]["verdict"] == "session"
    assert results["edit"]["verdict"] == "deny"
    assert results["edit"]["stack"] == "edit"
    assert results["edit"]["toplevels"] == 1
    assert results["edit"]["stack_after_back"] == "query"
    assert results["close"]["verdict"] == "deny"
    assert results["close"]["window_alive"] is True
    assert results["timeout"]["verdict"] == "deny"
    assert 1.5 <= results["timeout"]["elapsed"] <= 10
