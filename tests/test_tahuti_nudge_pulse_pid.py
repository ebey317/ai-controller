"""The nudge must not trust a stale /tmp/tahuti-pulse.pid whose pid was recycled."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import tahuti_nudge as nudge  # noqa: E402


def _point_pidfile_at(monkeypatch, tmp_path, pid):
    pidfile = tmp_path / "pulse.pid"
    pidfile.write_text(str(pid))
    real_path = nudge.Path

    def fake_path(p, *a):
        return real_path(pidfile if p == "/tmp/tahuti-pulse.pid" else p, *a)

    monkeypatch.setattr(nudge, "Path", fake_path)
    monkeypatch.setattr(nudge, "GOALS_PROCESSING", tmp_path / "none", raising=False)
    monkeypatch.setattr(nudge, "CAPTION", tmp_path / "nocaption", raising=False)
    monkeypatch.setattr(
        nudge.subprocess,
        "run",
        lambda *a, **k: type("R", (), {"stdout": ""})(),
    )


def test_live_pid_that_is_not_the_avatar_does_not_count_as_busy(monkeypatch, tmp_path):
    _point_pidfile_at(monkeypatch, tmp_path, os.getpid())  # alive, but pytest
    assert "avatar" not in nudge._busy_reason()


def test_real_avatar_pid_still_counts_as_busy(monkeypatch, tmp_path):
    _point_pidfile_at(monkeypatch, tmp_path, os.getpid())
    monkeypatch.setattr(
        "avatar_pulse.avatar_pid_is_safe", lambda pid, cmd, uid: True
    )
    assert "avatar" in nudge._busy_reason()
