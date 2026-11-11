#!/usr/bin/env python3
"""Voice FX -- the shared post-synthesis render chain for previews.

Every knob on the voice board has to be REAL, so the tuning that the
engines don't do natively happens here, in one place, on the rendered
WAV -- before playback, for both engines:

    raw engine render -> pitch -> bass/treble EQ -> gain -> out

- pitch: resample by 2**(semitones/12) (the same math the Kokoro
  preview helper originally did inline; it lives here now so Piper
  renders get real pitch too -- the piper binary has no pitch flag).
- tone: a simple two-band EQ. A one-pole lowpass at ~200 Hz gives
  the bass band; the signal minus a one-pole lowpass at ~4 kHz gives
  the treble band; each band is scaled by its dB gain and remixed.
  Shelf-style, gentle, and honest -- documented as exactly this.
- gain: plain multiplier (volume), soft-clipped to [-1, 1].

controller_setup.speak_preview() runs this as a subprocess (system
python3 has numpy + soundfile on this box) so the overlay and any
future speaker inherit the same chain by calling the same function.
Neutral settings (pitch 0, EQ flat, gain 1.0) are a bit-exact pass-
through copy -- callers may skip the subprocess entirely then.

CLI: python3 voice_fx.py IN.wav OUT.wav [--pitch ST] [--bass DB]
     [--treble DB] [--gain X]
"""

import argparse
import sys

import numpy as np

BASS_CUTOFF_HZ = 200.0
TREBLE_CUTOFF_HZ = 4000.0


def pitch_shift(samples, semitones):
    """Resample by the semitone factor; +12 = one octave up, half
    as long. Works on mono (N,) or multi-channel (N, C) arrays."""
    if not semitones:
        return samples
    factor = 2.0 ** (float(semitones) / 12.0)
    n = len(samples)
    idx = np.arange(0, n, factor)
    base = np.arange(n)
    if samples.ndim == 1:
        return np.interp(idx, base, samples).astype(samples.dtype)
    out = np.empty((len(idx), samples.shape[1]), dtype=samples.dtype)
    for ch in range(samples.shape[1]):
        out[:, ch] = np.interp(idx, base, samples[:, ch])
    return out


def _lowpass(samples, sr, cutoff_hz):
    """One-pole lowpass, per channel. alpha from the RC time
    constant -- the standard cheap shelf-band extractor."""
    dt = 1.0 / float(sr)
    rc = 1.0 / (2.0 * np.pi * cutoff_hz)
    alpha = dt / (rc + dt)
    out = np.empty_like(samples, dtype=np.float64)
    if samples.ndim == 1:
        acc = float(samples[0]) if len(samples) else 0.0
        for i, x in enumerate(samples):
            acc += alpha * (float(x) - acc)
            out[i] = acc
    else:
        acc = (
            samples[0].astype(np.float64).copy()
            if len(samples)
            else np.zeros(samples.shape[1])
        )
        for i in range(len(samples)):
            acc += alpha * (samples[i].astype(np.float64) - acc)
            out[i] = acc
    return out


def apply_tone(samples, sr, bass_db=0.0, treble_db=0.0):
    """Two-band EQ: bass shelf band ~200 Hz, treble band ~4 kHz."""
    if not bass_db and not treble_db:
        return samples
    x = samples.astype(np.float64)
    out = x
    if bass_db:
        low = _lowpass(x, sr, BASS_CUTOFF_HZ)
        out = out + (10.0 ** (float(bass_db) / 20.0) - 1.0) * low
    if treble_db:
        high = x - _lowpass(x, sr, TREBLE_CUTOFF_HZ)
        out = out + (10.0 ** (float(treble_db) / 20.0) - 1.0) * high
    return out.astype(np.float32)


def apply_fx(samples, sr, pitch_semitones=0.0, bass_db=0.0, treble_db=0.0, gain=1.0):
    """The full chain, in render order. Returns float32 samples."""
    out = np.asarray(samples, dtype=np.float32)
    out = pitch_shift(out, pitch_semitones)
    out = apply_tone(out, sr, bass_db, treble_db)
    if gain != 1.0:
        out = (out * float(gain)).astype(np.float32)
    return np.clip(out, -1.0, 1.0).astype(np.float32)


def is_neutral(pitch_semitones=0.0, bass_db=0.0, treble_db=0.0, gain=1.0):
    return not pitch_semitones and not bass_db and not treble_db and gain == 1.0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("in_wav")
    ap.add_argument("out_wav")
    ap.add_argument("--pitch", type=float, default=0.0, help="pitch shift in semitones")
    ap.add_argument("--bass", type=float, default=0.0, help="bass gain in dB")
    ap.add_argument("--treble", type=float, default=0.0, help="treble gain in dB")
    ap.add_argument("--gain", type=float, default=1.0, help="volume multiplier")
    args = ap.parse_args(argv)
    import soundfile as sf

    samples, sr = sf.read(args.in_wav, dtype="float32", always_2d=False)
    out = apply_fx(
        samples,
        sr,
        pitch_semitones=args.pitch,
        bass_db=args.bass,
        treble_db=args.treble,
        gain=args.gain,
    )
    sf.write(args.out_wav, out, sr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
