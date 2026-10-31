"""Tests for controller_setup.py -- doctor checks + Telegram audit.

Hermetic: every check runs against temp files, never the real
~/.master_ai_keys, and nothing here touches the network (the audit's
API rows are exercised only through the key-presence paths).
"""

import sys
from pathlib import Path

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
