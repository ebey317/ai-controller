"""Tests for the Avatar setup picker -- menu 3's entry point.

Lijah (2026-10-31): pressing 3 dropped him straight into the
active avatar's settings with no list to choose from. The picker
is step one now: the numbered portrait list shows FIRST, the pick
sets the active avatar, and only then do that avatar's settings
open. These tests drive the real functions with scripted input.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import controller_setup as cs  # noqa: E402
import voice_profiles as vp  # noqa: E402

PORTRAITS = ("tahuti", "seba", "kunoichi", "ogun")


@pytest.fixture()
def avatar_env(tmp_path, monkeypatch):
    """Fake portrait dir + a throwaway profile file, via the env
    overrides voice_profiles already honors."""
    portraits = tmp_path / "avatars"
    portraits.mkdir()
    for name in PORTRAITS:
        (portraits / f"{name}.webp").write_bytes(b"fake")
    monkeypatch.setenv("AI_CONTROLLER_AVATARS_DIR", str(portraits))
    profile = tmp_path / "voice-profiles.json"
    monkeypatch.setenv("AI_CONTROLLER_VOICE_PROFILES", str(profile))
    return vp.load_profiles(str(profile))


def _script_input(monkeypatch, answers):
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(it))


def test_picker_lists_all_portraits_first(avatar_env, monkeypatch, capsys):
    _script_input(monkeypatch, ["0"])
    assert cs._pick_avatar(avatar_env) is None
    out = capsys.readouterr().out
    for name in PORTRAITS:
        assert name in out
    # It is a picker, not a settings dump.
    assert "pick the avatar to set up" in out
    assert "Show-when trigger" not in out


def test_pick_sets_active_and_returns_name(avatar_env, monkeypatch):
    names = vp.available_avatars()
    pick = str(names.index("seba") + 1)
    _script_input(monkeypatch, [pick])
    assert cs._pick_avatar(avatar_env) == "seba"
    assert avatar_env["avatar"]["active_avatar"] == "seba"


def test_setup_flow_list_then_settings(avatar_env, monkeypatch, capsys):
    names = vp.available_avatars()
    pick = str(names.index("kunoichi") + 1)
    # pick kunoichi -> settings menu -> 0 back to list -> 0 back out
    _script_input(monkeypatch, [pick, "0", "0"])
    cs._avatar_setup(avatar_env)
    out = capsys.readouterr().out
    list_at = out.index("pick the avatar to set up")
    settings_at = out.index("-- avatar setup: kunoichi --")
    assert list_at < settings_at
    assert "Tune kunoichi's voice" in out
    assert avatar_env["avatar"]["active_avatar"] == "kunoichi"
