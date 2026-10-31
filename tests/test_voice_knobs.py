"""Tests for voice_knobs.py -- the voice board's pure core.

The board is GTK, but every value <-> dial-position decision lives
in voice_knobs so it can be pinned headlessly: endpoints, clamping,
per-engine applicability, and the digital readouts.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import voice_knobs as vk  # noqa: E402
import voice_profiles as vp  # noqa: E402


def test_every_profile_field_has_exactly_one_knob():
    fields = [s.field for s in vk.KNOB_SPECS]
    assert len(fields) == len(set(fields))
    assert set(fields) == {"rate", "pitch_semitones"} | set(vp.KNOB_FIELDS)


def test_fraction_endpoints_and_midpoint():
    rate = vk.SPECS_BY_FIELD["rate"]
    assert rate.fraction(rate.lo) == 0.0
    assert rate.fraction(rate.hi) == 1.0
    assert abs(rate.fraction(1.25) - 0.5) < 1e-9
    assert abs(rate.value_at(0.5) - 1.25) < 1e-9


def test_fraction_value_round_trip():
    for spec in vk.KNOB_SPECS:
        for frac in (0.0, 0.25, 0.5, 0.73, 1.0):
            assert abs(spec.fraction(spec.value_at(frac)) - frac) < 1e-9


def test_angle_sweep_is_270_degrees():
    pitch = vk.SPECS_BY_FIELD["pitch_semitones"]
    assert pitch.angle(pitch.lo) == -135.0
    assert pitch.angle(pitch.hi) == 135.0
    assert pitch.angle(0.0) == 0.0  # neutral pitch points straight up


def test_clamp_holds_bounds():
    gain = vk.SPECS_BY_FIELD["gain"]
    assert gain.clamp(99) == gain.hi
    assert gain.clamp(-3) == gain.lo
    assert gain.fraction(99) == 1.0


def test_noise_knobs_are_piper_only():
    for field in ("noise_scale", "noise_w"):
        spec = vk.SPECS_BY_FIELD[field]
        assert spec.applies_to("piper")
        assert not spec.applies_to("kokoro")
        assert not spec.applies_to("edge-tts")
    for field in ("rate", "pitch_semitones", "gain", "bass_db",
                  "treble_db"):
        spec = vk.SPECS_BY_FIELD[field]
        assert spec.applies_to("kokoro")
        assert spec.applies_to("piper")


def test_readout_formats():
    assert vk.format_value(vk.SPECS_BY_FIELD["rate"], 1.07) == "1.07x"
    assert vk.format_value(
        vk.SPECS_BY_FIELD["pitch_semitones"], -2.0) == "-2.0 st"
    assert vk.format_value(vk.SPECS_BY_FIELD["gain"], 1.0) == "100%"
    assert vk.format_value(vk.SPECS_BY_FIELD["gain"], 0.5) == "50%"
    assert vk.format_value(vk.SPECS_BY_FIELD["bass_db"], 3) == "+3 dB"
    assert vk.format_value(
        vk.SPECS_BY_FIELD["noise_scale"], 0.667) == "0.67"


def test_profile_values_fill_neutrals():
    values = vk.profile_values({"rate": 1.07, "pitch_semitones": 0.0})
    assert values["rate"] == 1.07
    assert values["gain"] == 1.0
    assert values["noise_scale"] == vp.NOISE_SCALE_DEFAULT
    tuned = vk.profile_values({"rate": 1.0, "pitch_semitones": 0.0,
                               "bass_db": -4.0, "gain": 1.5})
    assert tuned["bass_db"] == -4.0
    assert tuned["gain"] == 1.5
