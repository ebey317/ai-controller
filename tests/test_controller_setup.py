"""Tests for controller_setup.py -- doctor checks + Telegram audit.

Hermetic: every check runs against temp files, never the real
~/.master_ai_keys, and nothing here touches the network (the audit's
API rows are exercised only through the key-presence paths).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import controller_setup as cs  # noqa: E402
import voice_profiles as vp  # noqa: E402


def test_key_names_never_values(tmp_path):
    keys = tmp_path / "keys"
    keys.write_text(
        "TELEGRAM_BOT_TOKEN=123:secret-value\n"
        "TELEGRAM_CHAT_ID=4242\n"
        "# a comment\n"
        "OTHER_KEY=x\n"
    )
    names = cs.read_key_names(str(keys))
    assert names == {"TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "OTHER_KEY"}
    # The audit output must never contain a secret value.
    rows = cs.telegram_audit(str(keys))
    blob = " ".join(detail for _, _, detail in rows)
    assert "secret-value" not in blob
    assert "123:secret" not in blob


def test_audit_fails_without_token(tmp_path):
    keys = tmp_path / "keys"
    keys.write_text("SOMETHING_ELSE=x\n")
    rows = cs.telegram_audit(str(keys))
    assert rows[0][0] == cs.FAIL
    assert "TELEGRAM_BOT_TOKEN" in rows[0][2]


def test_audit_reports_unwired_features(tmp_path):
    keys = tmp_path / "keys"
    keys.write_text("TELEGRAM_BOT_TOKEN=t\nTELEGRAM_CHAT_ID=1\n")
    rows = cs.telegram_audit(str(keys))
    infos = {label for level, label, _ in rows if level == "INFO"}
    # The honest not-yet-wired list stays visible in the audit.
    assert "voice notes in" in infos
    assert "files out" in infos
    assert "reactions" in infos


def test_every_menu_command_is_handled():
    """The registered "/" menu is a promise: a handler backs each entry."""
    for name, desc in cs.EXPECTED_BOT_COMMANDS:
        assert name in cs.GATEWAY_HANDLED_COMMANDS
        assert name == name.lower() and " " not in name  # BotCommand rules
        assert desc  # every menu entry needs a description


def test_doctor_profile_check_missing_file(tmp_path):
    level, label, detail = cs.check_profile_file(str(tmp_path / "nope.json"))
    assert level == cs.WARN
    assert "defaults" in detail


def test_doctor_profile_check_valid(tmp_path):
    path = str(tmp_path / "voice-profiles.json")
    vp.save_profiles(vp.load_profiles(path), path)
    level, label, detail = cs.check_profile_file(path)
    assert level == cs.OK
    assert "am_onyx" in detail  # Tahuti's locked voice shows in the report
    assert "1.07" in detail


def test_doctor_profile_check_corrupt(tmp_path):
    path = tmp_path / "voice-profiles.json"
    path.write_text('{"version": 1, "avatars": {"tahuti": {"engine": 5}}}')
    level, _, _ = cs.check_profile_file(str(path))
    assert level == cs.FAIL


def test_doctor_kokoro_missing_home(tmp_path):
    level, _, detail = cs.check_kokoro(str(tmp_path))
    assert level == cs.FAIL
    assert "missing" in detail


# ── avatar setup (wizard section 3 / doctor "avatar setup") ─────────

def _portraits(tmp_path, *names):
    d = tmp_path / "avatars"
    d.mkdir()
    for n in names:
        (d / f"{n}.webp").write_bytes(b"portrait")
    return str(d)


def test_available_avatars_lists_portraits(tmp_path):
    d = _portraits(tmp_path, "tahuti", "seba", "ogun")
    (tmp_path / "avatars" / "notes.md").write_text("not a portrait")
    assert vp.available_avatars(d) == ["ogun", "seba", "tahuti"]


def test_avatar_defaults_match_lijah_rules():
    s = vp.get_avatar_settings(vp.load_profiles("/nonexistent.json"))
    assert s["active_avatar"] == "tahuti"
    # Presence is talk-driven by default (Lijah, 2026-10-31).
    assert s["show_trigger"] == "talk_buttons"
    assert s["enabled_by_default"] is True  # feature armed
    assert 180 <= s["size_px"] <= 260  # small-companion class
    assert s["movement"] == "corner_loop"
    assert s["respect_notification_corner"] is True
    assert s["approval_box"] is True


def test_avatar_trigger_round_trip_both_modes(tmp_path):
    path = str(tmp_path / "voice-profiles.json")
    for mode in ("talk_buttons", "always_on"):
        data = vp.load_profiles(path)
        data["avatar"]["show_trigger"] = mode
        data["avatar"]["active_avatar"] = "seba"
        vp.save_profiles(data, path)
        loaded = vp.load_profiles(path)
        assert loaded["avatar"]["show_trigger"] == mode
        assert loaded["avatar"]["active_avatar"] == "seba"
        label = vp.SHOW_TRIGGER_LABELS[mode]
        assert label  # doctor/wizard summary must have words for it


def test_avatar_bad_values_rejected(tmp_path):
    path = str(tmp_path / "voice-profiles.json")
    data = vp.load_profiles(path)
    data["avatar"]["show_trigger"] = "sometimes"
    data["avatar"]["size_px"] = 4000
    data["avatar"]["movement"] = "teleports"
    problems = vp.validate_profiles(data)
    assert any("show_trigger" in p for p in problems)
    assert any("size_px" in p for p in problems)
    assert any("movement" in p for p in problems)
    try:
        vp.save_profiles(data, path)
        raise AssertionError("save_profiles should have raised")
    except ValueError:
        pass


def test_doctor_avatar_ok(tmp_path):
    path = str(tmp_path / "voice-profiles.json")
    vp.save_profiles(vp.load_profiles(path), path)
    d = _portraits(tmp_path, "tahuti", "seba", "kunoichi", "ogun")
    level, label, detail = cs.check_avatar(path, d)
    assert level == cs.OK
    assert "tahuti" in detail
    assert "talk-button" in detail  # trigger mode reported in plain words
    assert "220px" in detail


def test_doctor_avatar_missing_portrait_fails(tmp_path):
    path = str(tmp_path / "voice-profiles.json")
    data = vp.load_profiles(path)
    data["avatar"]["active_avatar"] = "ghost"
    vp.save_profiles(data, path)
    d = _portraits(tmp_path, "tahuti")
    level, _, detail = cs.check_avatar(path, d)
    assert level == cs.FAIL
    assert "ghost" in detail


def test_doctor_avatar_always_on_reported(tmp_path):
    path = str(tmp_path / "voice-profiles.json")
    data = vp.load_profiles(path)
    data["avatar"]["show_trigger"] = "always_on"
    vp.save_profiles(data, path)
    d = _portraits(tmp_path, "tahuti")
    level, _, detail = cs.check_avatar(path, d)
    assert level == cs.OK
    assert "always on" in detail


def test_validate_avatar_settings_flags_unknown_active(tmp_path):
    d = _portraits(tmp_path, "tahuti")
    settings = vp.get_avatar_settings(vp.DEFAULT_PROFILES)
    settings["active_avatar"] = "nobody"
    problems = vp.validate_avatar_settings(settings, d)
    assert problems and "nobody" in problems[0]


# ── avatar visibility lifecycle ─────────────────────────────────────

def test_lifecycle_defaults_all_on():
    s = vp.get_avatar_settings(vp.load_profiles("/nonexistent.json"))
    for key in vp.LIFECYCLE_KEYS:
        assert s[key] is True, key


def test_lifecycle_round_trip(tmp_path):
    path = str(tmp_path / "voice-profiles.json")
    data = vp.load_profiles(path)
    data["avatar"]["stay_while_working"] = False
    data["avatar"]["fade_when_done"] = False
    vp.save_profiles(data, path)
    s = vp.get_avatar_settings(vp.load_profiles(path))
    assert s["stay_while_working"] is False
    assert s["fade_when_done"] is False
    assert s["show_on_talk"] is True  # untouched keys keep defaults
    assert s["popup_on_approval"] is True


def test_lifecycle_bad_value_rejected(tmp_path):
    data = vp.load_profiles(str(tmp_path / "x.json"))
    data["avatar"]["popup_on_approval"] = "yes please"
    problems = vp.validate_profiles(data)
    assert any("popup_on_approval" in p for p in problems)


def test_doctor_reports_lifecycle(tmp_path):
    path = str(tmp_path / "voice-profiles.json")
    vp.save_profiles(vp.load_profiles(path), path)
    d = _portraits(tmp_path, "tahuti")
    level, _, detail = cs.check_avatar(path, d)
    assert level == cs.OK
    for word in ("talk pop-up on", "stays while working on",
                 "fades when done on", "approval pop-up on"):
        assert word in detail, word


# ── voice board ─────────────────────────────────────────────────────

def test_voice_board_doctor_ok(tmp_path):
    path = str(tmp_path / "voice-profiles.json")
    vp.save_profiles(vp.load_profiles(path), path)
    level, label, detail = cs.check_voice_board(path)
    assert level == cs.OK
    assert label == "voice board"
    assert "tahuti" in detail and "gain" in detail


def test_voice_board_doctor_reports_tuned_values(tmp_path):
    path = str(tmp_path / "voice-profiles.json")
    data = vp.load_profiles(path)
    data["avatars"]["tahuti"]["bass_db"] = 6.0
    vp.save_profiles(data, path)
    level, _, detail = cs.check_voice_board(path)
    assert level == cs.OK
    assert "bass +6dB" in detail


def test_voice_board_doctor_fails_on_corrupt_file(tmp_path):
    path = tmp_path / "voice-profiles.json"
    path.write_text("{not json")
    level, _, _ = cs.check_voice_board(str(path))
    assert level == cs.FAIL


def test_piper_argv_carries_the_noise_knobs():
    argv = cs._piper_argv("piper", "model.onnx", "out.wav", 1.25,
                          {"noise_scale": 0.5, "noise_w": 0.25})
    assert argv[argv.index("--length-scale") + 1] == "0.8000"
    assert argv[argv.index("--noise-scale") + 1] == "0.500"
    assert argv[argv.index("--noise-w-scale") + 1] == "0.250"


def test_apply_fx_neutral_copies_render(tmp_path):
    raw = tmp_path / "raw.wav"
    raw.write_bytes(b"RIFF-fake-but-bytes")
    out = tmp_path / "out.wav"
    profile = {"pitch_semitones": 0.0}
    assert cs._apply_fx(str(raw), str(out), profile) == str(out)
    assert out.read_bytes() == b"RIFF-fake-but-bytes"


def test_apply_fx_runs_the_chain(tmp_path):
    np = pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")
    sr = 24000
    t = np.arange(sr // 2) / sr
    tone = (0.4 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    raw = str(tmp_path / "raw.wav")
    sf.write(raw, tone, sr)
    out = str(tmp_path / "out.wav")
    profile = {"pitch_semitones": 0.0, "gain": 0.5}
    assert cs._apply_fx(raw, out, profile) == out
    samples, _ = sf.read(out, dtype="float32")
    assert float(np.max(np.abs(samples))) == pytest.approx(0.2, abs=0.02)


# ── Edge demo voice (doctor row) ─────────────────────────────────

def test_doctor_edge_demo_ok(tmp_path):
    path = str(tmp_path / "voice-profiles.json")
    vp.save_profiles(vp.load_profiles(path), path)
    level, label, detail = cs.check_edge_demo(
        path, voice_names=["en-US-ChristopherNeural",
                           "en-US-AriaNeural"])
    assert level == cs.OK
    assert label == "edge demo voice"
    assert "en-US-ChristopherNeural" in detail
    assert "locked" in detail


def test_doctor_edge_demo_missing_voice_warns_not_fails(tmp_path):
    path = str(tmp_path / "voice-profiles.json")
    vp.save_profiles(vp.load_profiles(path), path)
    level, _, detail = cs.check_edge_demo(path, voice_names=[])
    assert level == cs.WARN  # optional bridge: never a hard failure
    assert "en-US-ChristopherNeural" in detail
