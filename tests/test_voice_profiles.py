"""Tests for voice_profiles.py -- the ONE shared voice profile file.

The keyboard TTS and the avatars read the same file; these tests pin
the round-trip, the locked Tahuti defaults, and the validation rules.
"""

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import voice_profiles as vp  # noqa: E402


def test_defaults_carry_locked_tahuti(tmp_path):
    """A missing file yields defaults with Lijah's locked Tahuti pick."""
    data = vp.load_profiles(str(tmp_path / "voice-profiles.json"))
    tahuti = vp.get_avatar_profile(data, "tahuti")
    assert tahuti == {
        "engine": "kokoro",
        "voice": "am_onyx",
        "pitch_semitones": 0.0,
        "rate": 1.07,
    }
    # Avatar overlay present by default when the keyboard is up.
    assert data["avatar"]["enabled_by_default"] is True
    assert data["avatar"]["scale"] == 1.0


def test_round_trip_save_load(tmp_path):
    path = str(tmp_path / "voice-profiles.json")
    data = vp.load_profiles(path)
    data["avatars"]["tahuti"]["rate"] = 1.21
    data["avatars"]["seba"] = {
        "engine": "piper", "voice": "joe",
        "pitch_semitones": 0.0, "rate": 1.0,
    }
    data["keyboard"]["rate"] = 1.1
    data["avatar"]["scale"] = 1.5
    vp.save_profiles(data, path)

    loaded = vp.load_profiles(path)
    assert loaded["avatars"]["tahuti"]["rate"] == 1.21
    assert loaded["avatars"]["tahuti"]["voice"] == "am_onyx"  # untouched
    assert loaded["avatars"]["seba"]["engine"] == "piper"
    assert loaded["keyboard"]["rate"] == 1.1
    assert loaded["avatar"]["scale"] == 1.5
    # The file on disk is plain JSON with the same content.
    on_disk = json.loads(Path(path).read_text())
    assert on_disk["avatars"]["tahuti"]["rate"] == 1.21


def test_partial_file_merges_over_defaults(tmp_path):
    path = tmp_path / "voice-profiles.json"
    path.write_text(json.dumps({
        "version": 1,
        "avatars": {"tahuti": {"rate": 0.95}},
    }))
    data = vp.load_profiles(str(path))
    tahuti = vp.get_avatar_profile(data, "tahuti")
    assert tahuti["rate"] == 0.95
    assert tahuti["engine"] == "kokoro"      # default survived the merge
    assert tahuti["voice"] == "am_onyx"


def test_validation_rejects_bad_values():
    data = copy.deepcopy(vp.DEFAULT_PROFILES)
    data["avatars"]["tahuti"]["engine"] = "not-an-engine"
    data["avatars"]["tahuti"]["rate"] = 9.9
    data["avatar"]["scale"] = 0.01
    problems = vp.validate_profiles(data)
    assert any("engine" in p for p in problems)
    assert any("rate" in p for p in problems)
    assert any("scale" in p for p in problems)


def test_save_refuses_invalid(tmp_path):
    data = copy.deepcopy(vp.DEFAULT_PROFILES)
    data["keyboard"]["pitch_semitones"] = 99
    with pytest.raises(ValueError):
        vp.save_profiles(data, str(tmp_path / "voice-profiles.json"))


def test_corrupt_file_raises(tmp_path):
    path = tmp_path / "voice-profiles.json"
    path.write_text(json.dumps({"version": 1, "avatars": {
        "tahuti": {"engine": "kokoro", "voice": "am_onyx",
                   "pitch_semitones": 0.0, "rate": "fast"}}}))
    with pytest.raises(ValueError):
        vp.load_profiles(str(path))


def test_unknown_avatar_gets_neutral_default():
    data = vp.load_profiles("/nonexistent/voice-profiles.json")
    prof = vp.get_avatar_profile(data, "ogun")
    assert prof["rate"] == 1.0
    assert prof["pitch_semitones"] == 0.0


def test_preview_line_for_tahuti():
    assert vp.preview_line("tahuti").startswith("I am Tahuti.")
