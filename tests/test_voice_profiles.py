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
        "engine": "piper",
        "voice": "joe",
        "pitch_semitones": 0.0,
        "rate": 1.0,
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
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "avatars": {"tahuti": {"rate": 0.95}},
            }
        )
    )
    data = vp.load_profiles(str(path))
    tahuti = vp.get_avatar_profile(data, "tahuti")
    assert tahuti["rate"] == 0.95
    assert tahuti["engine"] == "kokoro"  # default survived the merge
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
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "avatars": {
                    "tahuti": {
                        "engine": "kokoro",
                        "voice": "am_onyx",
                        "pitch_semitones": 0.0,
                        "rate": "fast",
                    }
                },
            }
        )
    )
    with pytest.raises(ValueError):
        vp.load_profiles(str(path))


def test_unknown_avatar_gets_neutral_default():
    data = vp.load_profiles("/nonexistent/voice-profiles.json")
    prof = vp.get_avatar_profile(data, "ogun")
    assert prof["rate"] == 1.0
    assert prof["pitch_semitones"] == 0.0


def test_preview_line_for_tahuti():
    assert vp.preview_line("tahuti").startswith("I am Tahuti.")


# ── voice-board knobs ────────────────────────────────────────────────


def test_knob_values_defaults_are_neutral():
    """A profile that never opened the board gets engine neutrals."""
    assert vp.knob_values({}) == {
        "gain": 1.0,
        "bass_db": 0.0,
        "treble_db": 0.0,
        "noise_scale": vp.NOISE_SCALE_DEFAULT,
        "noise_w": vp.NOISE_W_DEFAULT,
    }
    assert vp.knob_values({"gain": 1.4})["gain"] == 1.4


def test_knob_fields_round_trip(tmp_path):
    path = str(tmp_path / "voice-profiles.json")
    data = vp.load_profiles(path)
    knobs = {
        "gain": 1.3,
        "bass_db": -3.0,
        "treble_db": 4.5,
        "noise_scale": 0.5,
        "noise_w": 0.25,
    }
    data["avatars"]["tahuti"].update(knobs)
    data["keyboard"].update(knobs)
    vp.save_profiles(data, path)
    loaded = vp.load_profiles(path)
    assert vp.knob_values(loaded["avatars"]["tahuti"]) == knobs
    assert vp.knob_values(loaded["keyboard"]) == knobs
    # The locked Tahuti pick itself is untouched by the new fields.
    tahuti = loaded["avatars"]["tahuti"]
    assert tahuti["engine"] == "kokoro"
    assert tahuti["voice"] == "am_onyx"
    assert tahuti["rate"] == 1.07
    assert tahuti["pitch_semitones"] == 0.0


def test_knob_bounds_rejected():
    bad_values = {
        "gain": 2.5,
        "bass_db": -20.0,
        "treble_db": 16.0,
        "noise_scale": 1.5,
        "noise_w": -0.1,
    }
    for field, bad in bad_values.items():
        data = copy.deepcopy(vp.DEFAULT_PROFILES)
        data["avatars"]["tahuti"][field] = bad
        problems = vp.validate_profiles(data)
        assert any(field in p for p in problems), field
        data = copy.deepcopy(vp.DEFAULT_PROFILES)
        data["keyboard"][field] = bad
        problems = vp.validate_profiles(data)
        assert any(field in p for p in problems), field


def test_save_refuses_out_of_bounds_knob(tmp_path):
    data = copy.deepcopy(vp.DEFAULT_PROFILES)
    data["avatars"]["tahuti"] = {
        "engine": "piper",
        "voice": "joe",
        "pitch_semitones": 0.0,
        "rate": 1.0,
        "gain": 9.9,
    }
    with pytest.raises(ValueError):
        vp.save_profiles(data, str(tmp_path / "voice-profiles.json"))


def test_profile_without_knob_keys_still_valid():
    """Files written before the board existed must validate."""
    data = copy.deepcopy(vp.DEFAULT_PROFILES)
    assert vp.validate_profiles(data) == []
    assert vp.knob_values(data["avatars"]["tahuti"])["gain"] == 1.0


# ── Edge demo bridge (Tahuti voice loop) ─────────────────────────


def test_edge_demo_preset_values_and_lock_unchanged(tmp_path):
    path = str(tmp_path / "voice-profiles.json")
    data = vp.load_profiles(path)
    demo = data["avatars"]["tahuti_demo"]
    assert demo == vp.TAHUTI_DEMO_PROFILE
    assert demo == {
        "engine": "edge",
        "voice": "en-US-ChristopherNeural",
        "pitch_semitones": 0.0,
        "rate": 1.07,
    }
    assert vp.canonical_engine(demo["engine"]) == "edge"
    # The demo preset never moves Tahuti's locked local default.
    assert data["avatars"]["tahuti"] == {
        "engine": "kokoro",
        "voice": "am_onyx",
        "pitch_semitones": 0.0,
        "rate": 1.07,
    }
    vp.save_profiles(data, path)
    loaded = vp.load_profiles(path)
    assert loaded["avatars"]["tahuti_demo"] == demo
    assert loaded["avatars"]["tahuti"]["engine"] == "kokoro"


def test_edge_engine_spellings_both_validate():
    assert "edge" in vp.ENGINES and "edge-tts" in vp.ENGINES
    assert vp.canonical_engine("edge-tts") == "edge"
    assert vp.canonical_engine("edge") == "edge"
    assert vp.canonical_engine("kokoro") == "kokoro"
    data = copy.deepcopy(vp.DEFAULT_PROFILES)
    data["avatars"]["seba"] = {
        "engine": "edge-tts",
        "voice": "en-US-GuyNeural",
        "pitch_semitones": 0.0,
        "rate": 1.0,
    }
    assert vp.validate_profiles(data) == []
