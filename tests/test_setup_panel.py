"""Tests for the GTK3 setup panel and its slide-keyboard wiring.

Hermetic: UI logic is tested without a display where possible. Where the
registry is involved, tests either use controlled ``Backend`` entries or
the real registry with temp files/monkeypatching, never mock-only
progress or invented status.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import controller_setup as cs  # noqa: E402

pytest.importorskip("gi")
import setup_panel as sp  # noqa: E402

# ── registry-driven health gathering ───────────────────────────────────


def test_gather_health_uses_registry_and_groups_by_category(monkeypatch):
    calls = []

    def ok_check():
        calls.append("ok")
        return (cs.OK, "ok-backend", "all good")

    def warn_check():
        calls.append("warn")
        return (cs.WARN, "warn-backend", "needs attention")

    monkeypatch.setattr(
        cs,
        "BACKENDS",
        [
            cs.Backend("ok-one", "controller", "live", ok_check),
            cs.Backend("warn-one", "audio-voice", "live", warn_check),
        ],
    )
    health = sp.gather_health()
    assert len(health) == 2
    cats = [h["category_key"] for h in health]
    assert cats == ["controller", "audio-voice"]
    assert health[0]["rows"][0]["level"] == cs.OK
    assert health[1]["rows"][0]["level"] == cs.WARN
    assert calls == ["ok", "warn"]


def test_gather_health_flattens_list_results(monkeypatch):
    def multi_check():
        return [
            (cs.OK, "svc-a", "active"),
            (cs.WARN, "svc-b", "inactive"),
        ]

    monkeypatch.setattr(
        cs,
        "BACKENDS",
        [cs.Backend("services", "services", "external", multi_check)],
    )
    health = sp.gather_health()
    assert len(health) == 1
    rows = health[0]["rows"]
    assert len(rows) == 2
    assert rows[0]["label"] == "svc-a"
    assert rows[1]["label"] == "svc-b"


def test_gather_health_contains_backend_source(monkeypatch):
    def check():
        return (cs.OK, "x", "y")

    monkeypatch.setattr(
        cs,
        "BACKENDS",
        [cs.Backend("x", "controller", "optional", check)],
    )
    row = sp.gather_health()[0]["rows"][0]
    assert row["source"] == "optional"


def test_gather_health_with_real_registry_runs_doctor_rows(tmp_path, monkeypatch):
    """Use the real registry but keep it filesystem-only (no services/HTTP)."""
    # Point voice-profiles and avatars at temp dirs so the test is hermetic.
    monkeypatch.setenv(
        "AI_CONTROLLER_VOICE_PROFILES", str(tmp_path / "voice-profiles.json")
    )
    avatars = tmp_path / "avatars"
    avatars.mkdir()
    (avatars / "tahuti.webp").write_bytes(b"portrait")
    monkeypatch.setenv("AI_CONTROLLER_AVATARS_DIR", str(avatars))
    # Disable external/optional checks that need network or systemctl.
    keep = [b for b in cs.BACKENDS if b.source == "live"]
    monkeypatch.setattr(cs, "BACKENDS", keep)

    health = sp.gather_health()
    categories = {h["category_key"] for h in health}
    assert "controller" in categories
    for h in health:
        for row in h["rows"]:
            assert row["level"] in (cs.OK, cs.WARN, cs.FAIL)
            assert row["label"]


# ── support-output redaction ──────────────────────────────────────────


def test_redact_detail_obscures_home_path(tmp_path):
    home = str(tmp_path / "home" / "elijah")
    detail = f"file at {home}/.config/ai-controller/config.env"
    redacted = sp.redact_detail(detail, home_path=home)
    assert home not in redacted
    assert "~/.config/ai-controller/config.env" in redacted


def test_redacted_doctor_text_uses_run_doctor(monkeypatch):
    monkeypatch.setattr(
        cs,
        "BACKENDS",
        [cs.Backend("demo", "controller", "live", lambda: (cs.OK, "demo", "fine"))],
    )
    text = sp.redacted_doctor_text(home_path="/nowhere")
    assert "OK" in text
    assert "demo" in text
    assert "fine" in text


# ── consumer apply status mapping ────────────────────────────────────


def test_consumer_apply_status_ready_next_use():
    for key in ("ptt_mode", "input_target", "keyboard_pack", "keyboard_rate"):
        assert sp.consumer_apply_status(key) == "Ready for your next use"


def test_consumer_apply_status_ready_next_approval():
    for key in (
        "avatar_active",
        "avatar_size_px",
        "avatar_movement",
        "avatar_respect_corner",
        "avatar_notification_corner",
        "avatar_approval_box",
        "avatar_popup_approval",
    ):
        assert (
            sp.consumer_apply_status(key)
            == "Ready the next time Tahuti asks for approval"
        )


def test_consumer_apply_status_not_connected_tahuti():
    for key in (
        "tahuti_engine",
        "tahuti_voice",
        "tahuti_rate",
        "tahuti_pitch",
        "tahuti_gain",
        "avatar_show_on_talk",
        "avatar_stay_working",
        "avatar_fade_done",
    ):
        assert sp.consumer_apply_status(key) == (
            "Not connected to Tahuti yet — its running voice loop uses "
            "tahuti_demo, not tahuti"
        )


def test_consumer_apply_status_unmapped_is_truthful_not_ready():
    assert sp.consumer_apply_status("keyboard_pitch") == (
        "Not ready yet — no live consumer reads 'keyboard_pitch'"
    )
    assert sp.consumer_apply_status("avatar_enabled") == (
        "Not ready yet — no live consumer reads 'avatar_enabled'"
    )
    assert sp.consumer_apply_status("no_such_key") == (
        "Not ready yet — no live consumer reads 'no_such_key'"
    )


# ── action availability ───────────────────────────────────────────────


def test_has_display_true_when_display_env_set(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    assert sp.has_display()


def test_has_display_false_without_display_env(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert not sp.has_display()


def test_open_voice_board_returns_outcome(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    # launch_voice_board tries to Popen the board; avoid spawning a window.
    monkeypatch.setattr(
        cs, "launch_voice_board", lambda: "voice board would open on :0"
    )
    assert "voice board" in sp.open_voice_board().lower()


def test_open_wizard_in_terminal_returns_message_when_no_terminal(monkeypatch):
    monkeypatch.setattr(sp.shutil, "which", lambda _name: None)
    result = sp.open_wizard_in_terminal()
    assert "no supported terminal" in result.lower()


# ── slide-keyboard wiring ─────────────────────────────────────────────


def test_slide_keyboard_launcher_keeps_keyboard_source_separate():
    """The themed keyboard launches the setup wizard as a separate process,
    not an imported overlay (SETUP now opens controller_setup.py in a
    terminal -- see commit c516a41 -- rather than the GTK panel directly)."""
    import slide_keyboard as sk

    source = open(sk.__file__, encoding="utf-8").read()
    assert '"controller_setup.py"' in source
    assert "subprocess.Popen" in source
    assert "import setup_panel" not in source


def test_slide_keyboard_setup_method_launches_page_process(monkeypatch):
    """SETUP opens the terminal wizard when a terminal emulator is available."""
    import slide_keyboard as sk

    launches = []
    monkeypatch.setattr(
        sk.shutil,
        "which",
        lambda name: "/usr/bin/gnome-terminal" if name == "gnome-terminal" else None,
    )
    monkeypatch.setattr(sk.subprocess, "Popen", lambda args: launches.append(args))

    sk.SlideKeyboard._on_setup(object(), None)
    assert len(launches) == 1
    assert launches[0][0] == "gnome-terminal"
    assert any("controller_setup.py" in arg for arg in launches[0])


def test_slide_keyboard_setup_method_falls_back_without_terminal(monkeypatch):
    """SETUP falls back to a direct (non-interactive) launch when no
    terminal emulator is found, instead of silently doing nothing."""
    import slide_keyboard as sk

    launches = []
    monkeypatch.setattr(sk.shutil, "which", lambda name: None)
    monkeypatch.setattr(sk.subprocess, "Popen", lambda args: launches.append(args))

    sk.SlideKeyboard._on_setup(object(), None)
    assert len(launches) == 1
    assert launches[0][0] == sk.sys.executable
    assert launches[0][1].endswith("scripts/controller_setup.py")


def test_unbacked_setup_controls_are_read_only(monkeypatch, tmp_path):
    """Cards without a live consumer must be read-only, not editable."""
    monkeypatch.setenv(
        "AI_CONTROLLER_VOICE_PROFILES", str(tmp_path / "voice-profiles.json")
    )
    avatars = tmp_path / "avatars"
    avatars.mkdir()
    (avatars / "tahuti.webp").write_bytes(b"portrait")
    monkeypatch.setenv("AI_CONTROLLER_AVATARS_DIR", str(avatars))

    import setup_panel as sp

    recorded = []

    class Recorder:
        def __init__(self, section, title, description, current_value):
            self.title = title
            self.editable = False
            self.readonly = False
            self.reason = None
            recorded.append(self)

        def add_editable(self, *args, **kwargs):
            self.editable = True

        def add_readonly(self, reason):
            self.readonly = True
            self.reason = reason

    monkeypatch.setattr(sp, "_Card", Recorder)
    monkeypatch.setattr(sp.SetupCenter, "_section_box", lambda self, title: [])

    class FakeCenter(sp.SetupCenter):
        def __init__(self, parent=None):
            pass

    center = FakeCenter()
    center._settings = sp.current_settings()
    center._build_voice_section()
    center._build_tahuti_section()

    by_title = {r.title: r for r in recorded}

    for title in (
        "Keyboard pitch",
        "Keyboard volume / gain",
        "Show trigger",
        "Avatar armed",
    ):
        card = by_title[title]
        assert card.readonly, f"{title} should be read-only"
        assert not card.editable, f"{title} must not offer an editable control"

    assert "pitch" in by_title["Keyboard pitch"].reason.lower()
    assert "gain" in by_title["Keyboard volume / gain"].reason.lower()
    assert "show_trigger" in by_title["Show trigger"].reason.lower()
    assert "enabled_by_default" in by_title["Avatar armed"].reason.lower()

    # Proven-backed controls must stay editable/applyable.
    for title in (
        "Keyboard voice pack",
        "Keyboard speaking speed",
        "Tahuti speaking speed",
        "Tahuti volume / gain",
        "Avatar size",
        "Active avatar",
    ):
        assert by_title[title].editable, f"{title} must remain editable"


def test_apply_setting_backed_controls_round_trip(monkeypatch, tmp_path):
    """Editable settings still apply and read back from the profiles file."""
    profile_path = tmp_path / "voice-profiles.json"
    monkeypatch.setenv("AI_CONTROLLER_VOICE_PROFILES", str(profile_path))

    import voice_profiles as vp

    initial = vp.load_profiles(str(profile_path))
    initial["keyboard"]["rate"] = 1.0
    initial["avatar"]["size_px"] = 220
    vp.save_profiles(initial, str(profile_path))

    ok, rb1, msg1 = sp.apply_setting("keyboard_rate", 1.5)
    assert ok
    assert rb1 == pytest.approx(1.5)

    ok, rb2, msg2 = sp.apply_setting("avatar_size_px", 250)
    assert ok
    assert rb2 == 250

    reloaded = vp.load_profiles(str(profile_path))
    assert reloaded["keyboard"]["rate"] == pytest.approx(1.5)
    assert reloaded["avatar"]["size_px"] == 250


# ── UI quality/safety fixes ────────────────────────────────────────────


def test_apply_setting_keyboard_pack_empty_voice_packs_permits_only_current(
    monkeypatch, tmp_path
):
    """When no voice packs are discovered, only the current pack may be kept."""
    monkeypatch.setenv(
        "AI_CONTROLLER_VOICE_PROFILES", str(tmp_path / "voice-profiles.json")
    )
    monkeypatch.setattr(sp, "_discover_voice_packs", lambda: [])

    ok, rb, msg = sp.apply_setting("keyboard_pack", "aria")
    assert ok is True
    assert rb == "aria"

    ok, rb, msg = sp.apply_setting("keyboard_pack", "joe")
    assert ok is False
    assert rb == "aria"
    assert "no voice packs installed" in msg.lower()


def test_apply_setting_avatar_active_no_portraits_permits_only_current(
    monkeypatch, tmp_path
):
    """When no avatar portraits are on disk, only the current avatar may be kept."""
    monkeypatch.setenv(
        "AI_CONTROLLER_VOICE_PROFILES", str(tmp_path / "voice-profiles.json")
    )
    monkeypatch.setattr(sp.vp, "available_avatars", lambda: [])

    ok, rb, msg = sp.apply_setting("avatar_active", "tahuti")
    assert ok is True
    assert rb == "tahuti"

    ok, rb, msg = sp.apply_setting("avatar_active", "seba")
    assert ok is False
    assert rb == "tahuti"
    assert "no portraits installed" in msg.lower()


def test_apply_setting_keyboard_pack_state_file_failure_returns_failure(
    monkeypatch, tmp_path
):
    """A failure writing the ai_controller_voice state file must not report success."""
    monkeypatch.setenv(
        "AI_CONTROLLER_VOICE_PROFILES", str(tmp_path / "voice-profiles.json")
    )
    monkeypatch.setattr(sp, "config_dir", lambda: str(tmp_path))
    monkeypatch.setattr(sp, "_discover_voice_packs", lambda: ["aria"])

    state_file = str(tmp_path / "ai_controller_voice")
    real_open = open

    def selective_open(file, mode="r", *args, **kwargs):
        path = str(file)
        if path == state_file and "w" in str(mode):
            raise OSError(f"denied: {path}")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", selective_open)

    ok, rb, msg = sp.apply_setting("keyboard_pack", "aria")
    assert ok is False
    assert rb == "aria"
    assert "could not be written" in msg.lower()


def test_refresh_gathers_data_in_worker_then_idle_rebuilds(monkeypatch):
    """Refresh must gather non-GTK data in the worker and rebuild via GLib.idle_add."""
    import threading

    class FakeCenter:
        def _gather_data(self):
            return {}

        def _rebuild_sections(self, *args):
            pass

        def _set_status(self, text):
            pass

    center = FakeCenter()
    idle_calls = []
    monkeypatch.setattr(
        sp.GLib,
        "idle_add",
        lambda fn, *args: idle_calls.append((fn.__name__, args)) or 0,
    )

    class ImmediateThread:
        def __init__(self, target, daemon=False):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(threading, "Thread", ImmediateThread)

    fixed = {"settings": {"dummy": 1}, "health": [], "sensei": (None, None, None)}
    monkeypatch.setattr(center, "_gather_data", lambda: fixed)
    status_calls = []

    def _set_status(text):
        status_calls.append(text)

    monkeypatch.setattr(center, "_set_status", _set_status)

    sp.SetupCenter._on_refresh(center, None)
    assert status_calls == ["Refreshing..."]
    assert idle_calls == [
        ("_rebuild_sections", (fixed["settings"], fixed["health"], fixed["sensei"])),
        ("_set_status", ("Setup Center refreshed.",)),
    ]


def test_rebuild_sections_preserves_vertical_scroll_position(monkeypatch):
    """Rebuilding sections should restore the previous vertical scroll offset."""

    class FakeVAdj:
        def __init__(self, value=100.0, upper=500.0, page_size=50.0):
            self.value = value
            self.upper = upper
            self.page_size = page_size

        def get_value(self):
            return self.value

        def set_value(self, v):
            self.value = v

        def get_upper(self):
            return self.upper

        def get_page_size(self):
            return self.page_size

    class FakeScrolled:
        def __init__(self, vadj):
            self._vadj = vadj

        def get_vadjustment(self):
            return self._vadj

    class FakeBox:
        def __init__(self):
            self.children = []

        def get_children(self):
            return list(self.children)

        def remove(self, child):
            self.children.remove(child)

        def pack_start(self, *a):
            pass

        def show_all(self):
            pass

    center = type("FakeCenter", (), {})()
    vadj = FakeVAdj(value=123.0)
    center._scrolled = FakeScrolled(vadj)
    center.cards_box = FakeBox()
    center._health_rows_box = FakeBox()
    for sec in (
        "_build_controller_section",
        "_build_voice_section",
        "_build_tahuti_section",
        "_build_sensei_section",
        "_build_services_section",
    ):
        monkeypatch.setattr(center, sec, lambda: None, raising=False)

    sp.SetupCenter._rebuild_sections(center, {}, [], (None, None, None))
    assert vadj.value == 123.0


def test_diagnostics_dialog_is_nonmodal_no_run(monkeypatch):
    """Diagnostics dialog must be nonmodal, not accept focus, and never call run()."""

    class FakeBuffer:
        def __init__(self):
            self.text = None

        def set_text(self, text):
            self.text = text

    class FakeTextView:
        def __init__(self):
            self._buffer = FakeBuffer()

        def set_editable(self, v):
            pass

        def set_cursor_visible(self, v):
            pass

        def get_buffer(self):
            return self._buffer

    class FakeScrolledWindow:
        def __init__(self):
            self.child = None

        def add(self, child):
            self.child = child

        def set_vexpand(self, v):
            pass

    class FakeBox:
        def __init__(self):
            self.children = []

        def pack_start(self, child, *a):
            self.children.append(child)

        def show_all(self):
            pass

    class FakeDialog:
        last = None

        def __init__(self, **kwargs):
            FakeDialog.last = self
            self.kwargs = kwargs
            self.modal_set = []
            self.accept_focus_set = []
            self.connected = {}
            self.shown = False
            self.destroyed = False

        def set_modal(self, v):
            self.modal_set.append(v)

        def set_accept_focus(self, v):
            self.accept_focus_set.append(v)

        def set_default_size(self, w, h):
            pass

        def get_content_area(self):
            return FakeBox()

        def connect(self, signal, handler):
            self.connected[signal] = handler

        def show_all(self):
            self.shown = True

        def destroy(self):
            self.destroyed = True

    monkeypatch.setattr(sp, "redacted_doctor_text", lambda: "doctor text")
    monkeypatch.setattr(sp.Gtk, "Dialog", FakeDialog)
    monkeypatch.setattr(sp.Gtk, "ScrolledWindow", FakeScrolledWindow)
    monkeypatch.setattr(sp.Gtk, "TextView", FakeTextView)
    monkeypatch.setattr(sp.Gtk, "ResponseType", type("ResponseType", (), {"CLOSE": 1}))

    center = type("FakeCenter", (), {})()
    sp.SetupCenter._on_diagnostics(center, None)

    dialog = FakeDialog.last
    assert dialog is not None
    assert dialog.modal_set == [False]
    assert dialog.accept_focus_set == [False]
    assert "response" in dialog.connected
    assert dialog.shown is True
    assert not hasattr(dialog, "run")
    handler = dialog.connected["response"]
    handler(dialog, sp.Gtk.ResponseType.CLOSE)
    assert dialog.destroyed is True


def test_health_rows_detail_label_uses_pango_wrap_mode(monkeypatch):
    """Regression: detail labels in health rows must receive Pango.WrapMode.

    ``Gtk.Label.set_line_wrap_mode`` expects a Pango.WrapMode enum, not a
    Gtk.WrapMode value. Using Gtk.WrapMode.WORD_CHAR raises TypeError when
    GTK is initialized. This test is hermetic: it fakes the GTK/Pango
    namespace and exercises ``_render_health_rows`` without a display.
    """

    class FakePango:
        class WrapMode:
            WORD_CHAR = object()

    class FakeStyleContext:
        def add_class(self, name):
            pass

    class FakeLabel:
        instances = []

        def __init__(self, *args, **kwargs):
            FakeLabel.instances.append(self)
            self._wrap_mode = None

        def get_style_context(self):
            return FakeStyleContext()

        def set_halign(self, v):
            pass

        def set_line_wrap(self, v):
            pass

        def set_line_wrap_mode(self, mode):
            self._wrap_mode = mode

        def set_tooltip_text(self, t):
            pass

        def set_hexpand(self, v):
            pass

        def set_size_request(self, w, h):
            pass

    class FakeBox:
        def __init__(self, *args, **kwargs):
            self.children = []

        def get_style_context(self):
            return FakeStyleContext()

        def set_margin_bottom(self, v):
            pass

        def get_children(self):
            return list(self.children)

        def remove(self, child):
            self.children.remove(child)

        def pack_start(self, *args):
            pass

        def show_all(self):
            pass

    monkeypatch.setattr(sp, "Pango", FakePango)
    monkeypatch.setattr(sp.Gtk, "Label", FakeLabel)
    monkeypatch.setattr(sp.Gtk, "Box", lambda *args, **kwargs: FakeBox(*args, **kwargs))

    center = type("FakeCenter", (), {})()
    center._health_rows_box = FakeBox()
    center._health = [
        {
            "category_key": "controller",
            "category_label": "Controller",
            "rows": [
                {
                    "level": "OK",
                    "label": "demo",
                    "detail": "some detail text",
                    "backend": "demo-backend",
                    "source": "live",
                }
            ],
        }
    ]

    sp.SetupCenter._render_health_rows(center, FakeBox())

    detail_labels = [lbl for lbl in FakeLabel.instances if lbl._wrap_mode is not None]
    assert len(detail_labels) == 1
    assert detail_labels[0]._wrap_mode is FakePango.WrapMode.WORD_CHAR


# ── verified readback + consumer status ──────────────────────────────


def test_apply_setting_ptt_verified_and_appends_status(monkeypatch, tmp_path):
    monkeypatch.setattr(sp, "config_dir", lambda: str(tmp_path))
    ok, rb, msg = sp.apply_setting("ptt_mode", "bubbly")
    assert ok is True
    assert rb == "bubbly"
    assert "bubbly" in msg
    assert sp.consumer_apply_status("ptt_mode") in msg
    assert sp._load_ptt_mode() == "bubbly"


def test_apply_setting_keyboard_rate_verified_and_appends_status(monkeypatch, tmp_path):
    profile_path = tmp_path / "voice-profiles.json"
    monkeypatch.setenv("AI_CONTROLLER_VOICE_PROFILES", str(profile_path))
    ok, rb, msg = sp.apply_setting("keyboard_rate", 1.5)
    assert ok is True
    assert rb == pytest.approx(1.5)
    assert sp.consumer_apply_status("keyboard_rate") in msg
    reloaded = sp.vp.load_profiles(str(profile_path))
    assert reloaded["keyboard"]["rate"] == pytest.approx(1.5)


def test_apply_setting_tahuti_mismatch_returns_failure(monkeypatch, tmp_path):
    profile_path = tmp_path / "voice-profiles.json"
    monkeypatch.setenv("AI_CONTROLLER_VOICE_PROFILES", str(profile_path))
    monkeypatch.setattr(
        sp.vp,
        "save_profiles",
        lambda data, path=None: str(path or sp.vp.profiles_path()),
    )
    ok, rb, msg = sp.apply_setting("tahuti_rate", 1.5)
    assert ok is False
    assert rb == pytest.approx(1.07)
    assert "wrote" in msg.lower() and "source reads" in msg.lower()
    assert sp.consumer_apply_status("tahuti_rate") not in msg


def test_apply_setting_forced_source_read_failure_returns_failure(
    monkeypatch, tmp_path
):
    profile_path = tmp_path / "voice-profiles.json"
    monkeypatch.setenv("AI_CONTROLLER_VOICE_PROFILES", str(profile_path))
    real_load = sp.vp.load_profiles
    calls = []

    def failing_load(path=None):
        calls.append("call")
        if len(calls) == 1:
            return real_load(path)
        raise OSError("forced readback failure")

    monkeypatch.setattr(sp.vp, "load_profiles", failing_load)
    ok, rb, msg = sp.apply_setting("keyboard_rate", 1.5)
    assert ok is False
    assert rb is None
    assert "read back" in msg.lower()
    assert sp.consumer_apply_status("keyboard_rate") not in msg


# ── visible positioning above parent slide keyboard ────────────────────


def test_compute_setup_position_centers_above_parent():
    # Parent at (100, 400) size 680x276; setup 640x520; screen 1920x1080.
    pos = sp._compute_setup_position(
        (100, 400, 680, 276), (640, 520), (1920, 1080), gap=8
    )
    # Horizontally centered over parent: 100 + (680 - 640) / 2 = 120
    # Vertically above parent: 400 - 520 - 8 = -128 -> clamped to 0
    assert pos == (120, 0)


def test_compute_setup_position_clamps_left_edge():
    # Parent off the left side; setup must not go negative.
    pos = sp._compute_setup_position(
        (-100, 400, 680, 276), (640, 520), (1920, 1080), gap=8
    )
    assert pos[0] == 0
    assert pos[1] == 0


def test_compute_setup_position_clamps_right_edge():
    # Parent near right edge; setup must stay fully on screen.
    pos = sp._compute_setup_position(
        (1700, 400, 680, 276), (640, 520), (1920, 1080), gap=8
    )
    assert pos[0] == 1920 - 640
    assert pos[1] == 0


def test_compute_setup_position_clamps_bottom_edge():
    # Absurdly low parent; y must not push below screen bottom.
    pos = sp._compute_setup_position(
        (100, 2000, 680, 276), (640, 520), (1920, 1080), gap=8
    )
    assert pos[1] == 1080 - 520


def test_compute_setup_position_fallback_to_screen_center():
    pos = sp._compute_setup_position(None, (640, 520), (1920, 1080))
    assert pos == ((1920 - 640) // 2, (1080 - 520) // 2)


def test_compute_setup_position_custom_gap():
    # Room for both window and gap: y should be parent.y - setup_h - gap.
    pos = sp._compute_setup_position(
        (100, 900, 680, 276), (640, 520), (1920, 1080), gap=16
    )
    assert pos == (120, 900 - 520 - 16)


def test_show_setup_panel_does_not_present_or_grab_focus(monkeypatch):
    """The public entry point must only show and place, never raise focus."""
    calls = []

    class FakeCenter:
        def __init__(self, parent):
            self.parent_window = parent
            calls.append(("init", parent))

        def show_all(self):
            calls.append("show_all")

        def _place_window(self):
            calls.append("place_window")

        def present(self):
            calls.append("present")

        def grab_focus(self):
            calls.append("grab_focus")

    monkeypatch.setattr(sp, "SetupCenter", FakeCenter)
    sp._setup_panel_instance = None

    result = sp.show_setup_panel(parent="fake-parent")
    assert result is not None
    assert result.parent_window == "fake-parent"
    assert "show_all" in calls
    assert "place_window" in calls
    assert "present" not in calls
    assert "grab_focus" not in calls


def test_show_setup_panel_reuses_singleton_and_updates_parent(monkeypatch):
    """Repeated calls reuse one instance and can re-aim it at a new parent."""
    calls = []

    class FakeCenter:
        def __init__(self, parent):
            self.parent_window = parent
            calls.append(("init", parent))

        def show_all(self):
            calls.append("show_all")

        def _place_window(self):
            calls.append("place_window")

    monkeypatch.setattr(sp, "SetupCenter", FakeCenter)
    sp._setup_panel_instance = None

    sp.show_setup_panel(parent="first")
    sp.show_setup_panel(parent="second")
    assert calls.count(("init", "first")) == 1
    assert calls.count(("init", "second")) == 0
    assert sp._setup_panel_instance is not None
    assert sp._setup_panel_instance.parent_window == "second"


def test_place_window_uses_parent_geometry(monkeypatch):
    """_place_window reads parent position/size and calls move with clamped coords."""

    class FakeScreen:
        def get_width(self):
            return 1920

        def get_height(self):
            return 1080

    class FakeParent:
        def get_position(self):
            return (100, 500)

        def get_size(self):
            return (680, 276)

    class FakeCenter(sp.SetupCenter):
        def get_screen(self):
            return FakeScreen()

    center = FakeCenter.__new__(FakeCenter)
    center.WIDTH = 640
    center.HEIGHT = 520
    center.parent_window = FakeParent()
    moves = []
    center.move = lambda x, y: moves.append((x, y))

    sp.SetupCenter._place_window(center)
    # 100 + (680 - 640)/2 = 120; 500 - 520 - 8 = -28 -> 0
    assert moves == [(120, 0)]


def test_place_window_falls_back_to_screen_center(monkeypatch):
    """_place_window centers on screen when there is no parent."""

    class FakeScreen:
        def get_width(self):
            return 1920

        def get_height(self):
            return 1080

    class FakeCenter(sp.SetupCenter):
        def get_screen(self):
            return FakeScreen()

    center = FakeCenter.__new__(FakeCenter)
    center.WIDTH = 640
    center.HEIGHT = 520
    center.parent_window = None
    moves = []
    center.move = lambda x, y: moves.append((x, y))

    sp.SetupCenter._place_window(center)
    assert moves == [((1920 - 640) // 2, (1080 - 520) // 2)]


# ── keyboard voice-pack backend mapping ───────────────────────────────


def _make_voice_pack(tmp_path, pack_id, config):
    """Create voices/<pack_id>/config.json under a temp AI_CONTROLLER_DIR."""
    voices_dir = tmp_path / "voices" / pack_id
    voices_dir.mkdir(parents=True)
    (voices_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return voices_dir


def test_keyboard_voice_profile_aria_maps_to_edge_tts_voice(monkeypatch, tmp_path):
    """Aria pack resolves to the exact Edge TTS voice declared in its config."""
    monkeypatch.setenv("AI_CONTROLLER_DIR", str(tmp_path))
    _make_voice_pack(
        tmp_path,
        "aria",
        {
            "engine": "edge-tts",
            "voice": "en-US-AriaNeural",
        },
    )
    data = {"keyboard": {"pack": "aria", "rate": 1.0, "pitch_semitones": 0.0}}
    profile = sp._keyboard_voice_profile(data)
    assert profile["engine"] == "edge-tts"
    assert profile["voice"] == "en-US-AriaNeural"
    assert profile["rate"] == pytest.approx(1.0)


def test_keyboard_voice_profile_joe_maps_to_piper_pack_id(monkeypatch, tmp_path):
    """Joe Piper pack resolves engine piper and voice=pack id for _piper_model_for."""
    monkeypatch.setenv("AI_CONTROLLER_DIR", str(tmp_path))
    voices_dir = _make_voice_pack(
        tmp_path,
        "joe",
        {
            "engine": "piper",
            "voice": "en-US-JoeMedium",
            "model": "en_US-joe-medium.onnx",
        },
    )
    # Place the model file so _piper_model_for can resolve it from the pack dir.
    (voices_dir / "en_US-joe-medium.onnx").write_bytes(b"fake model")
    data = {"keyboard": {"pack": "joe", "rate": 1.1, "pitch_semitones": 1.0}}
    profile = sp._keyboard_voice_profile(data)
    assert profile["engine"] == "piper"
    assert profile["voice"] == "joe"
    # The pack id is what _piper_model_for needs to find the model.
    assert cs._piper_model_for(profile["voice"]) == str(
        voices_dir / "en_US-joe-medium.onnx"
    )


def test_keyboard_voice_profile_missing_engine_raises(monkeypatch, tmp_path):
    """A pack config without an engine is rejected with a clear error."""
    monkeypatch.setenv("AI_CONTROLLER_DIR", str(tmp_path))
    _make_voice_pack(tmp_path, "aria", {"voice": "en-US-AriaNeural"})
    with pytest.raises(sp.VoicePackConfigError, match="does not declare an engine"):
        sp._keyboard_voice_profile({"keyboard": {"pack": "aria"}})


def test_keyboard_voice_profile_edge_without_voice_raises(monkeypatch, tmp_path):
    """An edge pack config without a voice is rejected with a clear error."""
    monkeypatch.setenv("AI_CONTROLLER_DIR", str(tmp_path))
    _make_voice_pack(tmp_path, "aria", {"engine": "edge-tts"})
    with pytest.raises(sp.VoicePackConfigError, match="does not declare a voice"):
        sp._keyboard_voice_profile({"keyboard": {"pack": "aria"}})


def test_keyboard_voice_test_aria_profile_accepted_without_speech_or_network(
    monkeypatch, tmp_path
):
    """Aria profile is accepted by speak_preview without touching network/speech."""
    monkeypatch.setenv("AI_CONTROLLER_DIR", str(tmp_path))
    _make_voice_pack(
        tmp_path,
        "aria",
        {
            "engine": "edge-tts",
            "voice": "en-US-AriaNeural",
        },
    )
    monkeypatch.setenv("DISPLAY", ":0")

    captured = {}

    def fake_render_edge_profile(text, profile, out_path):
        captured["profile"] = profile
        captured["text"] = text
        Path(out_path).write_bytes(b"RIFF" + b"\x00" * 20)
        return out_path

    monkeypatch.setattr(cs.vl, "render_edge_profile", fake_render_edge_profile)
    monkeypatch.setattr(cs, "_play", lambda _path: None)

    result = sp._run_keyboard_voice_test()
    assert result == "Voice test played."
    assert captured["profile"]["engine"] == "edge-tts"
    assert captured["profile"]["voice"] == "en-US-AriaNeural"


def test_keyboard_voice_test_joe_profile_accepted_without_speech_or_network(
    monkeypatch, tmp_path
):
    """Joe Piper profile is accepted by speak_preview without real synthesis."""
    monkeypatch.setenv("AI_CONTROLLER_DIR", str(tmp_path))
    voices_dir = _make_voice_pack(
        tmp_path,
        "joe",
        {
            "engine": "piper",
            "voice": "en-US-JoeMedium",
            "model": "en_US-joe-medium.onnx",
        },
    )
    (voices_dir / "en_US-joe-medium.onnx").write_bytes(b"fake model")
    monkeypatch.setenv("DISPLAY", ":0")

    profile_path = tmp_path / "voice-profiles.json"
    profile_path.write_text(
        json.dumps({"version": 1, "keyboard": {"pack": "joe"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AI_CONTROLLER_VOICE_PROFILES", str(profile_path))

    piper_path = tmp_path / "fake_piper"
    piper_path.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "out = sys.argv[sys.argv.index('--output_file') + 1]\n"
        "with open(out, 'wb') as f:\n"
        "    f.write(b'RIFF' + b'\\x00' * 20)\n",
        encoding="utf-8",
    )
    piper_path.chmod(0o755)

    def fake_which(cmd):
        return str(piper_path) if cmd == "piper" else None

    monkeypatch.setattr(cs.shutil, "which", fake_which)
    monkeypatch.setattr(cs, "_play", lambda _path: None)

    result = sp._run_keyboard_voice_test()
    assert result == "Voice test played."


def test_keyboard_voice_test_missing_config_disabled_not_faked(monkeypatch, tmp_path):
    """Missing voice-pack config disables the test with an honest message."""
    monkeypatch.setenv("AI_CONTROLLER_DIR", str(tmp_path))
    result = sp._run_keyboard_voice_test()
    assert result.startswith("Voice test disabled:")
    assert "has no config.json" in result


def test_keyboard_voice_test_malformed_config_disabled_not_faked(monkeypatch, tmp_path):
    """Malformed JSON in the pack config disables the test with an honest message."""
    monkeypatch.setenv("AI_CONTROLLER_DIR", str(tmp_path))
    voices_dir = tmp_path / "voices" / "aria"
    voices_dir.mkdir(parents=True)
    (voices_dir / "config.json").write_text("not json", encoding="utf-8")
    result = sp._run_keyboard_voice_test()
    assert result.startswith("Voice test disabled:")
    assert "invalid JSON" in result
