#!/usr/bin/env python3
"""Voice-board knob definitions -- the radio panel's data model.

Lijah (2026-10-31): "i need my voice pitches, tones, pace, everything
to be editable. everything i can edit on a voice, i want it editable.
make it look like a radio knobs. i don't want slides, i want knobs."

This module is the board's pure core: which knobs exist, what profile
field each one turns, its range, its neutral (engine-default) value,
and the value <-> dial-position math. It is stdlib-only and imports
no GUI toolkit, so tests exercise the whole mapping headlessly; the
GTK board in voice_board.py is a thin skin over these specs.

Honesty rule: a knob exists only if the value really reaches the
render. Pace/pitch/volume/bass/treble apply to every engine through
the shared render chain (Kokoro natively + voice_fx post-processing;
Piper via --length-scale + voice_fx). Expression (noise_scale) and
Variation (noise_w) are Piper-only -- the piper binary takes
--noise-scale / --noise-w-scale; Kokoro's ONNX wrapper exposes no
equivalent, so those two knobs grey out on Kokoro instead of lying.
"""

from dataclasses import dataclass

import voice_profiles as vp

# Dial geometry: a radio knob sweeps 270 degrees, from -135 (min,
# pointing down-left) through 0 (top) to +135 (max, down-right).
SWEEP_DEGREES = 270.0
START_DEGREES = -135.0


@dataclass(frozen=True)
class KnobSpec:
    field: str          # profile field the knob writes
    label: str          # plain-English board label
    lo: float
    hi: float
    neutral: float      # engine-default value (double-click reset
                        # target is the SAVED value; this is the
                        # factory neutral used for new profiles)
    fmt: str            # readout style key, see format_value()
    engines: tuple = ()  # empty = every engine; else engine names

    def clamp(self, value):
        return max(self.lo, min(self.hi, float(value)))

    def fraction(self, value):
        """Value -> dial position 0.0..1.0."""
        if self.hi == self.lo:
            return 0.0
        return (self.clamp(value) - self.lo) / (self.hi - self.lo)

    def value_at(self, fraction):
        """Dial position 0.0..1.0 -> value (inverse of fraction())."""
        fraction = max(0.0, min(1.0, float(fraction)))
        return self.lo + fraction * (self.hi - self.lo)

    def angle(self, value):
        """Value -> indicator angle in degrees (-135..+135)."""
        return START_DEGREES + self.fraction(value) * SWEEP_DEGREES

    def applies_to(self, engine):
        return not self.engines or engine in self.engines


KNOB_SPECS = (
    KnobSpec("rate", "Pace", vp.RATE_MIN, vp.RATE_MAX, 1.0, "rate"),
    KnobSpec("pitch_semitones", "Pitch",
             vp.PITCH_MIN, vp.PITCH_MAX, 0.0, "pitch"),
    KnobSpec("gain", "Volume",
             vp.GAIN_MIN, vp.GAIN_MAX, vp.GAIN_DEFAULT, "gain"),
    KnobSpec("bass_db", "Bass",
             vp.BASS_MIN, vp.BASS_MAX, vp.BASS_DEFAULT, "db"),
    KnobSpec("treble_db", "Treble",
             vp.TREBLE_MIN, vp.TREBLE_MAX, vp.TREBLE_DEFAULT, "db"),
    KnobSpec("noise_scale", "Expression",
             vp.NOISE_SCALE_MIN, vp.NOISE_SCALE_MAX,
             vp.NOISE_SCALE_DEFAULT, "unit", engines=("piper",)),
    KnobSpec("noise_w", "Variation",
             vp.NOISE_W_MIN, vp.NOISE_W_MAX,
             vp.NOISE_W_DEFAULT, "unit", engines=("piper",)),
)

SPECS_BY_FIELD = {spec.field: spec for spec in KNOB_SPECS}


def knobs_for_engine(engine):
    """Specs in board order; non-applying specs are included (the
    board greys them) -- use spec.applies_to(engine) to decide."""
    return KNOB_SPECS


def format_value(spec, value):
    """Digital readout text under a knob."""
    value = spec.clamp(value)
    if spec.fmt == "rate":
        return f"{value:.2f}x"
    if spec.fmt == "pitch":
        return f"{value:+.1f} st"
    if spec.fmt == "gain":
        return f"{round(value * 100):d}%"
    if spec.fmt == "db":
        return f"{value:+.0f} dB"
    if spec.fmt == "unit":
        return f"{value:.2f}"
    return f"{value:g}"


def profile_values(profile):
    """Every knob field's current value for one voice profile:
    rate/pitch straight from the profile, the five extended fields
    through voice_profiles.knob_values() (neutrals filled)."""
    values = vp.knob_values(profile)
    values["rate"] = float(profile.get("rate", 1.0)) \
        if isinstance(profile, dict) else 1.0
    values["pitch_semitones"] = float(
        profile.get("pitch_semitones", 0.0)) \
        if isinstance(profile, dict) else 0.0
    return values
