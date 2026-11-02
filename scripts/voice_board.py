#!/usr/bin/env python3
"""Voice Board -- the radio-knob panel for AI Controller voices.

Lijah (2026-10-31): "i need my voice pitches, tones, pace,
everything to be editable. everything i can edit on a voice, i want
it editable. make it look like a radio knobs. i don't want slides,
i want knobs."

So: rotary knobs, no sliders anywhere. Drag a knob up/down (or use
the scroll wheel) to turn it; the digital readout under it tracks
the exact value; double-click snaps it back to the saved value.
Engine and voice stay selectors -- they are choices, not levels.

Every knob is real (see voice_knobs.py): Pace and Pitch, Volume,
Bass and Treble go through the shared render chain
(controller_setup.speak_preview -> voice_fx); the Piper-only
Expression / Variation knobs feed the piper binary's own
--noise-scale / --noise-w-scale flags and GREY OUT on Kokoro,
whose ONNX wrapper exposes no such controls. No fake knobs.

Launch:
    python3 scripts/voice_board.py
    (or setup wizard -> "Voice board (knobs)")
Headless visual check (no display needed):
    python3 scripts/voice_board.py --render-preview out.png

Saving writes the ONE shared profile
(~/.config/ai-controller/voice-profiles.json) -- the same file the
slide keyboard and the avatars read.
"""

import argparse
import copy
import math
import os
import sys
import threading

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import voice_knobs as vk  # noqa: E402
import voice_profiles as vp  # noqa: E402

ORANGE = (1.0, 0.416, 0.0)       # #FF6A00 -- the keyboard's accent
FACE = (0.137, 0.137, 0.169)     # #23232b
RIM = (0.35, 0.35, 0.42)
TICK = (0.55, 0.55, 0.60)
DIM = (0.30, 0.30, 0.34)

# Fixed voice list of the kokoro-v1.0 model on this box (from
# Kokoro.get_voices() in ~/tahuti-tts) -- the model version pins it.
KOKORO_VOICES = [
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica",
    "af_kore", "af_nicole", "af_nova", "af_river", "af_sarah",
    "af_sky", "am_adam", "am_echo", "am_eric", "am_fenrir",
    "am_liam", "am_michael", "am_onyx", "am_puck", "am_santa",
    "bf_alice", "bf_emma", "bf_isabella", "bf_lily", "bm_daniel",
    "bm_fable", "bm_george", "bm_lewis", "ef_dora", "em_alex",
    "em_santa", "ff_siwis", "hf_alpha", "hf_beta", "hm_omega",
    "hm_psi", "if_sara", "im_nicola", "jf_alpha", "jf_gongitsune",
    "jf_nezumi", "jf_tebukuro", "jm_kumo", "pf_dora", "pm_alex",
    "pm_santa", "zf_xiaobei", "zf_xiaoni", "zf_xiaoxiao",
    "zf_xiaoyi", "zm_yunjian", "zm_yunxi", "zm_yunxia",
    "zm_yunyang",
]
EDGE_VOICES = [
    "en-US-AriaNeural", "en-US-GuyNeural", "en-US-JennyNeural",
    "en-US-ChristopherNeural", "en-US-EricNeural",
    "en-US-MichelleNeural", "en-GB-SoniaNeural", "en-GB-RyanNeural",
]

KNOB_PX = 104


def _point(cx, cy, r, degrees):
    """Dial point: 0 deg = 12 o'clock, clockwise positive."""
    rad = math.radians(degrees)
    return cx + r * math.sin(rad), cy - r * math.cos(rad)


def draw_knob_face(cr, size, fraction, enabled=True):
    """Draw one knob face centered in a size x size square.

    Pure cairo -- used by the GTK widget AND by --render-preview,
    so the headless PNG is the real face, not a mock-up.
    """
    cx = cy = size / 2.0
    r = size / 2.0 - 6
    # tick ring across the 270-degree sweep
    cr.set_line_width(2.0)
    for i in range(11):
        deg = vk.START_DEGREES + i * (vk.SWEEP_DEGREES / 10.0)
        x1, y1 = _point(cx, cy, r + 1, deg)
        x2, y2 = _point(cx, cy, r + 5, deg)
        cr.set_source_rgb(*(TICK if enabled else DIM))
        cr.move_to(x1, y1)
        cr.line_to(x2, y2)
        cr.stroke()
    # body
    cr.set_source_rgb(*(FACE if enabled else (0.10, 0.10, 0.11)))
    cr.arc(cx, cy, r, 0, 2 * math.pi)
    cr.fill()
    cr.set_source_rgb(*(RIM if enabled else DIM))
    cr.set_line_width(2.0)
    cr.arc(cx, cy, r, 0, 2 * math.pi)
    cr.stroke()
    cr.set_source_rgb(0.20, 0.20, 0.24)
    cr.arc(cx, cy, r * 0.68, 0, 2 * math.pi)
    cr.fill()
    # indicator
    deg = vk.START_DEGREES + fraction * vk.SWEEP_DEGREES
    x1, y1 = _point(cx, cy, r * 0.30, deg)
    x2, y2 = _point(cx, cy, r * 0.88, deg)
    cr.set_source_rgb(*(ORANGE if enabled else DIM))
    cr.set_line_width(4.0)
    cr.set_line_cap(1)  # round
    cr.move_to(x1, y1)
    cr.line_to(x2, y2)
    cr.stroke()


def render_preview_png(path):
    """Headless board strip: every knob at a demo position, with
    labels + readouts, drawn by the same code the window uses."""
    import cairo
    specs = vk.KNOB_SPECS
    demo = {"rate": 1.07, "pitch_semitones": -2.0, "gain": 1.15,
            "bass_db": 3.0, "treble_db": -2.0, "noise_scale": 0.667,
            "noise_w": 0.8}
    cell_w, width = 130, 130 * len(specs) + 20
    height = 218
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    cr = cairo.Context(surface)
    cr.set_source_rgb(0.051, 0.051, 0.071)  # #0d0d12 board
    cr.paint()
    cr.set_source_rgb(*ORANGE)
    cr.set_font_size(15)
    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL,
                        cairo.FONT_WEIGHT_BOLD)
    cr.move_to(14, 26)
    cr.show_text("VOICE BOARD -- Tahuti (Kokoro am_onyx)")
    for i, spec in enumerate(specs):
        x0 = 10 + i * cell_w
        enabled = spec.applies_to("kokoro")
        # draw the face into the cell via a translated context
        cr.save()
        cr.translate(x0 + (cell_w - KNOB_PX) / 2.0, 40)
        draw_knob_face(cr, KNOB_PX,
                       spec.fraction(demo[spec.field]), enabled)
        cr.restore()
        cx = x0 + cell_w / 2.0
        cr.set_source_rgb(0.91, 0.91, 0.91)
        cr.set_font_size(13)
        label = spec.label + ("" if enabled else " (Piper)")
        w = cr.text_extents(label).width
        cr.move_to(cx - w / 2.0, 40 + KNOB_PX + 22)
        cr.show_text(label)
        cr.set_source_rgb(*(ORANGE if enabled else DIM))
        cr.set_font_size(12)
        readout = vk.format_value(spec, demo[spec.field])
        w = cr.text_extents(readout).width
        cr.move_to(cx - w / 2.0, 40 + KNOB_PX + 42)
        cr.show_text(readout)
    surface.write_to_png(path)
    return path


# ── GTK board ────────────────────────────────────────────────────────

def _gtk():
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GLib, Gtk
    return Gdk, GLib, Gtk


class Knob:
    """One rotary knob: DrawingArea face + label + digital readout."""

    def __init__(self, Gtk, Gdk, spec, on_change):
        self.Gdk = Gdk
        self.spec = spec
        self.on_change = on_change
        self.value = spec.neutral
        self.saved = spec.neutral
        self._drag = None
        self.area = Gtk.DrawingArea()
        self.area.set_size_request(KNOB_PX, KNOB_PX)
        self.area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.SCROLL_MASK)
        self.area.connect("draw", self._draw)
        self.area.connect("button-press-event", self._press)
        self.area.connect("button-release-event", self._release)
        self.area.connect("motion-notify-event", self._motion)
        self.area.connect("scroll-event", self._scroll)
        self.name_label = Gtk.Label(label=spec.label)
        self.value_label = Gtk.Label(label="")
        self.value_label.get_style_context().add_class("readout")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.pack_start(self.area, False, False, 0)
        box.pack_start(self.name_label, False, False, 0)
        box.pack_start(self.value_label, False, False, 0)
        self.widget = box
        self._refresh()

    def _refresh(self):
        self.value_label.set_text(vk.format_value(self.spec, self.value))
        self.area.queue_draw()

    def set_value(self, value, saved=None):
        self.value = self.spec.clamp(value)
        if saved is not None:
            self.saved = self.spec.clamp(saved)
        self._refresh()

    def set_enabled(self, enabled):
        self.area.set_sensitive(enabled)
        self.name_label.set_sensitive(enabled)
        self.value_label.set_sensitive(enabled)
        self.area.queue_draw()

    def _draw(self, _area, cr):
        alloc = self.area.get_allocation()
        draw_knob_face(cr, min(alloc.width, alloc.height),
                       self.spec.fraction(self.value),
                       self.area.get_sensitive())

    def _press(self, _area, event):
        if event.type == self.Gdk.EventType._2BUTTON_PRESS:
            self.set_value(self.saved)
            self.on_change(self.spec.field, self.value)
            return True
        self._drag = (event.y, self.spec.fraction(self.value))
        return True

    def _release(self, _area, _event):
        self._drag = None
        return True

    def _motion(self, _area, event):
        if self._drag is None:
            return False
        start_y, start_frac = self._drag
        # 180 px of upward drag sweeps the whole dial.
        frac = start_frac + (start_y - event.y) / 180.0
        self.set_value(self.spec.value_at(frac))
        self.on_change(self.spec.field, self.value)
        return True

    def _scroll(self, _area, event):
        _ok, _dx, dy = event.get_scroll_deltas()
        step = (self.spec.hi - self.spec.lo) / 40.0
        self.set_value(self.value + (-step if dy > 0 else step))
        self.on_change(self.spec.field, self.value)
        return True


class VoiceBoard:
    def __init__(self, Gtk, Gdk, GLib):
        self.Gtk, self.Gdk, self.GLib = Gtk, Gdk, GLib
        Gtk = self.Gtk
        try:
            self.data = vp.load_profiles()
        except ValueError:
            self.data = copy.deepcopy(vp.DEFAULT_PROFILES)
        self.targets = self._target_list()
        self.working = {}
        self.saved_profile = {}
        self.target = self.targets[0][0]

        self.win = Gtk.Window(title="Voice Board -- AI Controller")
        self.win.set_default_size(940, 430)
        self.win.connect("destroy", Gtk.main_quit)
        self._apply_css()

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.set_margin_top(10)
        root.set_margin_bottom(10)
        root.set_margin_start(12)
        root.set_margin_end(12)
        self.win.add(root)

        # selectors row: target / engine / voice (choices, not knobs)
        sel = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        sel.pack_start(Gtk.Label(label="Voice:"), False, False, 0)
        self.target_combo = Gtk.ComboBoxText()
        for _key, label in self.targets:
            self.target_combo.append_text(label)
        self.target_combo.set_active(0)
        sel.pack_start(self.target_combo, False, False, 0)
        sel.pack_start(Gtk.Label(label="Engine:"), False, False, 0)
        self.engine_combo = Gtk.ComboBoxText()
        for engine in vp.ENGINES:
            self.engine_combo.append_text(engine)
        sel.pack_start(self.engine_combo, False, False, 0)
        sel.pack_start(Gtk.Label(label="Voice id:"), False, False, 0)
        self.voice_combo = Gtk.ComboBoxText()
        self.voice_combo.set_entry_text_column(0)
        sel.pack_start(self.voice_combo, True, True, 0)
        root.pack_start(sel, False, False, 0)

        # the knobs -- one row, radio style
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.get_style_context().add_class("board")
        self.knobs = {}
        for spec in vk.KNOB_SPECS:
            knob = Knob(Gtk, Gdk, spec, self._on_knob)
            self.knobs[spec.field] = knob
            row.pack_start(knob.widget, True, False, 0)
        root.pack_start(row, False, False, 0)

        note = Gtk.Label(label="Expression + Variation are Piper "
                               "controls -- they grey out on Kokoro, "
                               "whose engine exposes no noise knobs.")
        note.set_halign(Gtk.Align.START)
        note.get_style_context().add_class("note")
        root.pack_start(note, False, False, 0)

        # test line + speak + save
        self.entry = Gtk.Entry()
        root.pack_start(self.entry, False, False, 0)
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                          spacing=8)
        self.speak_btn = Gtk.Button(label="SPEAK PREVIEW")
        self.speak_btn.get_style_context().add_class("speak")
        self.speak_btn.connect("clicked", self._on_speak)
        actions.pack_start(self.speak_btn, True, True, 0)
        self.save_btn = Gtk.Button(label="SAVE")
        self.save_btn.connect("clicked", self._on_save)
        actions.pack_start(self.save_btn, False, False, 0)
        root.pack_start(actions, False, False, 0)

        self.status = Gtk.Label(label="")
        self.status.set_halign(Gtk.Align.START)
        root.pack_start(self.status, False, False, 0)

        self._load_target(self.target)
        # Signals connect only now: set_active() during construction
        # must not fire _load_target before the knobs exist.
        self.target_combo.connect("changed", self._on_target)
        self.engine_combo.connect("changed", self._on_engine)
        self.voice_combo.connect("changed", self._on_voice)
        self.win.show_all()

    # ── data ─────────────────────────────────────────────────────────
    def _target_list(self):
        names = []
        for name in vp.available_avatars():
            if name not in names:
                names.append(name)
        for name in sorted(self.data.get("avatars", {})):
            if name not in names:
                names.append(name)
        if "tahuti" in names:
            names.remove("tahuti")
            names.insert(0, "tahuti")
        targets = [(n, n.capitalize()) for n in names]
        targets.append(("keyboard", "Keyboard voice"))
        return targets

    def _profile_for(self, target):
        if target == "keyboard":
            kb = self.data["keyboard"]
            prof = {"engine": "piper", "voice": kb["pack"],
                    "rate": kb["rate"],
                    "pitch_semitones": kb["pitch_semitones"]}
            prof.update(vp.knob_values(kb))
            return prof
        prof = dict(vp.get_avatar_profile(self.data, target))
        merged = {"engine": prof["engine"], "voice": prof["voice"],
                  "rate": prof["rate"],
                  "pitch_semitones": prof["pitch_semitones"]}
        merged.update(vp.knob_values(prof))
        return merged

    def _voices_for(self, engine, current):
        if engine == "kokoro":
            voices = list(KOKORO_VOICES)
        elif engine == "piper":
            voices = []
            from ai_controller_paths import ai_controller_dir
            voices_dir = os.path.join(ai_controller_dir(), "voices")
            if os.path.isdir(voices_dir):
                for name in sorted(os.listdir(voices_dir)):
                    if os.path.exists(os.path.join(
                            voices_dir, name, "config.json")):
                        voices.append(name)
            shared = os.path.expanduser("~/.local/share/piper")
            if os.path.isdir(shared):
                for fn in sorted(os.listdir(shared)):
                    if fn.endswith(".onnx"):
                        voices.append(fn)
            if not voices:
                voices = ["joe", "aria"]
        else:
            voices = list(EDGE_VOICES)
        if current and current not in voices:
            voices.insert(0, current)
        return voices

    def _load_target(self, target):
        self.target = target
        self.working = self._profile_for(target)
        self.saved_profile = copy.deepcopy(self.working)
        values = vk.profile_values(self.working)
        for field, knob in self.knobs.items():
            knob.set_value(values[field], saved=values[field])
        engine = self.working["engine"]
        self.engine_combo.set_active(vp.ENGINES.index(engine))
        self.engine_combo.set_sensitive(target != "keyboard")
        self._fill_voices(engine, self.working["voice"])
        self._knob_sensitivity(engine)
        default_line = vp.preview_line(
            "keyboard" if target == "keyboard" else target)
        self.entry.set_text(default_line)
        self.status.set_text(
            f"{target}: turn the knobs, SPEAK to hear them, "
            f"SAVE writes the shared profile.")

    def _fill_voices(self, engine, current):
        voices = self._voices_for(engine, current)
        self.voice_combo.remove_all()
        for voice in voices:
            self.voice_combo.append_text(voice)
        try:
            self.voice_combo.set_active(voices.index(current))
        except ValueError:
            self.voice_combo.set_active(0)

    def _knob_sensitivity(self, engine):
        for spec in vk.KNOB_SPECS:
            self.knobs[spec.field].set_enabled(spec.applies_to(engine))

    # ── events ───────────────────────────────────────────────────────
    def _on_target(self, combo):
        idx = combo.get_active()
        if idx >= 0:
            self._load_target(self.targets[idx][0])

    def _on_engine(self, combo):
        engine = combo.get_active_text()
        if not engine or engine == self.working.get("engine"):
            return
        self.working["engine"] = engine
        self._fill_voices(engine, self.working["voice"])
        self._knob_sensitivity(engine)

    def _on_voice(self, combo):
        voice = combo.get_active_text()
        if voice:
            self.working["voice"] = voice

    def _on_knob(self, field, value):
        self.working[field] = round(float(value), 3)

    def _on_speak(self, _btn):
        import controller_setup as cs
        profile = dict(self.working)
        text = self.entry.get_text().strip() or None
        name = "keyboard" if self.target == "keyboard" else self.target
        self.status.set_text("speaking...")
        self.speak_btn.set_sensitive(False)

        def run():
            ok = cs.speak_preview(profile, name, text=text)
            self.GLib.idle_add(self._speak_done, ok)

        threading.Thread(target=run, daemon=True).start()

    def _speak_done(self, ok):
        self.speak_btn.set_sensitive(True)
        self.status.set_text("preview played." if ok
                             else "preview failed -- see terminal.")

    def _on_save(self, _btn):
        w = self.working
        fields = ("rate", "pitch_semitones") + vp.KNOB_FIELDS
        if self.target == "keyboard":
            kb = {"pack": w["voice"]}
            for f in fields:
                kb[f] = round(float(w[f]), 3)
            self.data["keyboard"] = kb
        else:
            prof = {"engine": w["engine"], "voice": w["voice"]}
            for f in fields:
                prof[f] = round(float(w[f]), 3)
            self.data.setdefault("avatars", {})[self.target] = prof
        try:
            path = vp.save_profiles(self.data)
        except ValueError as e:
            self.status.set_text(f"NOT saved: {e}")
            return
        self.saved_profile = copy.deepcopy(self.working)
        values = vk.profile_values(self.working)
        for field, knob in self.knobs.items():
            knob.set_value(values[field], saved=values[field])
        self.status.set_text(f"saved to {path}")

    def _apply_css(self):
        Gtk = self.Gtk
        css = b"""
        window { background-color: #0d0d12; }
        label { color: #e8e8e8; }
        label.readout { font-family: monospace; color: #FF6A00;
                        font-weight: bold; }
        label.note { color: #8a8a92; font-size: 11px; }
        .board { background-color: #131319; border: 1px solid #2c2c36;
                 border-radius: 10px; padding: 8px; }
        button { background-image: none; background-color: #23232b;
                 color: #e8e8e8; border: 1px solid #3a3a44;
                 border-radius: 6px; padding: 6px 12px; }
        button:hover { background-color: #2f2f3a; }
        button.speak { background-color: #FF6A00; color: #0d0d12;
                       font-weight: bold; font-size: 14px; }
        entry { background-color: #17171d; color: #e8e8e8;
                border: 1px solid #3a3a44; border-radius: 6px; }
        combobox button { padding: 2px 8px; }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            self.Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--render-preview", metavar="PNG",
                    help="draw the knob strip to a PNG and exit "
                         "(no display needed)")
    args = ap.parse_args(argv)
    if args.render_preview:
        print(render_preview_png(args.render_preview))
        return 0
    Gdk, GLib, Gtk = _gtk()
    try:
        VoiceBoard(Gtk, Gdk, GLib)
    except Exception as e:
        print(f"the voice board needs a display ({e}).\n"
              f"Run it on the desktop: python3 "
              f"{os.path.abspath(__file__)}")
        return 2
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
