"""Tests for voice_fx.py -- the render chain behind the knobs.

Synthetic signals only: gain must scale, pitch must resample, and
the two EQ bands must move energy in the band they claim to own.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

np = pytest.importorskip("numpy")
import voice_fx as fx  # noqa: E402

SR = 24000


def _tone(freq, seconds=0.5, amp=0.4):
    t = np.arange(int(SR * seconds)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _band_mag(samples, freq):
    spectrum = np.abs(np.fft.rfft(samples.astype(np.float64)))
    freqs = np.fft.rfftfreq(len(samples), 1.0 / SR)
    return spectrum[np.argmin(np.abs(freqs - freq))]


def test_neutral_is_passthrough():
    x = _tone(440)
    out = fx.apply_fx(x, SR)
    assert np.allclose(out, x, atol=1e-6)
    assert fx.is_neutral(0.0, 0.0, 0.0, 1.0)
    assert not fx.is_neutral(0.0, 3.0, 0.0, 1.0)


def test_gain_scales_amplitude():
    x = _tone(440, amp=0.2)
    out = fx.apply_fx(x, SR, gain=2.0)
    assert np.max(np.abs(out)) == pytest.approx(0.4, abs=0.02)
    quiet = fx.apply_fx(x, SR, gain=0.5)
    assert np.max(np.abs(quiet)) == pytest.approx(0.1, abs=0.01)


def test_gain_cannot_clip_past_full_scale():
    x = _tone(440, amp=0.9)
    out = fx.apply_fx(x, SR, gain=2.0)
    assert np.max(np.abs(out)) <= 1.0


def test_pitch_octave_up_halves_length():
    x = _tone(440, seconds=1.0)
    up = fx.apply_fx(x, SR, pitch_semitones=12.0)
    assert len(up) == pytest.approx(len(x) / 2, rel=0.02)
    down = fx.apply_fx(x, SR, pitch_semitones=-12.0)
    assert len(down) == pytest.approx(len(x) * 2, rel=0.02)


def test_bass_knob_moves_low_band_only():
    x = _tone(100) + _tone(6000)
    before = _band_mag(x, 100) / _band_mag(x, 6000)
    boosted = fx.apply_fx(x, SR, bass_db=12.0)
    after = _band_mag(boosted, 100) / _band_mag(boosted, 6000)
    assert after > before * 1.8  # +12 dB is a ~4x band gain
    cut = fx.apply_fx(x, SR, bass_db=-12.0)
    assert _band_mag(cut, 100) / _band_mag(cut, 6000) < before * 0.6


def test_treble_knob_moves_high_band_only():
    x = _tone(100) + _tone(6000)
    before = _band_mag(x, 6000) / _band_mag(x, 100)
    boosted = fx.apply_fx(x, SR, treble_db=12.0)
    after = _band_mag(boosted, 6000) / _band_mag(boosted, 100)
    assert after > before * 1.8


def test_stereo_survives_the_chain():
    mono = _tone(440, amp=0.2)
    stereo = np.stack([mono, mono], axis=1)
    out = fx.apply_fx(stereo, SR, pitch_semitones=12.0, gain=1.5)
    assert out.ndim == 2 and out.shape[1] == 2
    assert len(out) == pytest.approx(len(mono) / 2, rel=0.02)
