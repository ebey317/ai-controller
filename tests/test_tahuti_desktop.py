"""Tests for the Tahuti real-desktop bridge.

The desktop primitives are exercised through a stubbed approval gate and
stubbed subprocess layer, so these tests never touch the operator's real
screen. The approval-fail-closed property is asserted directly, because
that is the property that makes this safe to ship: with no human present,
no action on the live desktop may proceed.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import tahuti_desktop as desktop  # noqa: E402
import tahuti_mcp as mcp  # noqa: E402


class _Verdict:
    """Mirrors the real gate's Verdict vocabulary.

    The production code compares the gate's return value against
    ``gate.Verdict.DENY`` / ``gate.Verdict.TIMEOUT``, so the fake has to
    expose the same attribute rather than bare strings.
    """

    DENY = "DENY"
    TIMEOUT = "TIMEOUT"
    ONCE = "ONCE"
    ALWAYS = "ALWAYS"
    SESSION = "SESSION"


class _FakeGate:
    """Stand-in for master-ai-cli's shared approval gate."""

    Verdict = _Verdict

    def __init__(self, verdict="ONCE"):
        self.verdict = verdict
        self.requests: list[tuple[str, str]] = []

    def request_approval(self, pattern_key, description, **kwargs):
        self.requests.append((pattern_key, description))
        return self.verdict

    def set_explicit_callback(self, cb):
        self.callback = cb


class _AvatarModule:
    """Mirrors tahuti_avatar.py's real public surface, including the
    voice_profiles-backed settings _install_tahuti_callback reads to
    decide whether to register a callback at all (2026-11-01)."""

    def __init__(self, approval_box=True, popup_on_approval=True):
        self._settings = {
            "approval_box": approval_box,
            "popup_on_approval": popup_on_approval,
        }

    def load_profiles(self):
        return {}

    def get_avatar_settings(self, _profiles):
        return self._settings

    TIMEOUT_SECONDS = 120

    @staticmethod
    def card_details(pattern_key, description, extra):
        return {
            "who": "Tahuti — your AI Controller",
            "what": description,
            "where": "Your active Linux desktop",
            "when": "Immediately after you choose Yes",
            "why": "test",
            "how": "test",
            "effect": "test",
            "pattern_key": pattern_key,
        }

    @staticmethod
    def prompt(pattern_key, description, **extra):
        return "once"


@pytest.fixture
def gate(monkeypatch):
    fake = _FakeGate()
    monkeypatch.setattr(desktop, "_APPROVAL", fake)
    return fake


# ──────────────────────────────────────────────────────────────────────
# Approval gate
# ──────────────────────────────────────────────────────────────────────


def test_native_callback_installs_on_existing_gate(monkeypatch):
    """Falls back to the standalone box when the real floating avatar
    (ninja.avatar_approval, a separate repo) isn't available -- which is
    always true in CI, and is forced here via the _ask_ninja_avatar seam
    so this test never reaches real GTK code even on a dev box where
    ~/master-ai-cli genuinely exists (found live 2026-11-02: without this
    mock the test opened a real window and hung waiting for a click)."""
    gate = _FakeGate()
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(desktop, "_import_from_path", lambda *_: _AvatarModule())
    monkeypatch.setattr(
        desktop,
        "_ask_ninja_avatar",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            RuntimeError("ninja not available in tests")
        ),
    )
    desktop._install_tahuti_callback(gate)
    assert (
        gate.callback("cua:type:foreground", "Type 1 character", action="type")
        == "once"
    )


def test_native_callback_skipped_when_approval_popup_disabled(monkeypatch):
    """Regression: disabling the avatar's approval popup must leave NO
    callback installed, not one that always denies -- a hijacked explicit
    callback would override approval_gate.request_approval()'s own
    per-call fail_closed_when_no_human policy for every pattern, including
    a caller that explicitly opted into permissive no-human behavior.
    Found and fixed 2026-11-01."""
    gate = _FakeGate()
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(
        desktop,
        "_import_from_path",
        lambda *_: _AvatarModule(approval_box=False, popup_on_approval=True),
    )
    desktop._install_tahuti_callback(gate)
    assert not hasattr(gate, "callback")


def test_native_callback_skipped_when_popup_on_approval_disabled(monkeypatch):
    """Same regression, the other flag: either setting disabled must
    skip installing a callback."""
    gate = _FakeGate()
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(
        desktop,
        "_import_from_path",
        lambda *_: _AvatarModule(approval_box=True, popup_on_approval=False),
    )
    desktop._install_tahuti_callback(gate)
    assert not hasattr(gate, "callback")


def test_pattern_key_is_scoped_by_action_and_mode():
    assert (
        desktop._pattern_key("left_click", "foreground") == "cua:left_click:foreground"
    )
    assert (
        desktop._pattern_key("left_click", "background") == "cua:left_click:background"
    )
    # A background grant must never cover a foreground action.
    assert desktop._pattern_key("left_click", "foreground") != desktop._pattern_key(
        "left_click", "background"
    )


def test_pattern_key_rejects_a_bad_mode():
    with pytest.raises(ValueError):
        desktop._pattern_key("left_click", "sideways")


def test_actions_fail_closed_with_no_human(gate):
    """No human present -> every action is refused. This is the property
    that makes an unattended run safe on the operator's live desktop."""
    gate.verdict = _Verdict.DENY
    for call in (
        lambda: desktop.screenshot(),
        lambda: desktop.left_click(10, 10),
        lambda: desktop.right_click(10, 10),
        lambda: desktop.move_cursor(10, 10),
        lambda: desktop.type_text("hello"),
        lambda: desktop.press_key("Return"),
        lambda: desktop.run_command("echo hi"),
    ):
        with pytest.raises(desktop.DesktopActionRefused):
            call()


def test_timeout_is_refused_not_treated_as_approval(gate):
    gate.verdict = _Verdict.TIMEOUT
    with pytest.raises(desktop.DesktopActionRefused):
        desktop.left_click(10, 10)


def test_refusal_reaches_the_gate_with_the_right_key(gate):
    gate.verdict = _Verdict.DENY
    with pytest.raises(desktop.DesktopActionRefused):
        desktop.type_text("hello")
    assert gate.requests == [("cua:type:foreground", "Type 5 character(s)")]


# ──────────────────────────────────────────────────────────────────────
# Screenshot
# ──────────────────────────────────────────────────────────────────────


def _fake_png(width: int = 40, height: int = 20) -> bytes:
    """A minimal valid PNG header (IHDR) plus filler.

    The PNG signature plus an IHDR-sized prefix is enough for the size
    reader; the bytes after it are never decoded in these tests.
    """
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def test_png_size_reads_ihdr():
    data = _fake_png(1280, 720)
    assert desktop._png_size(data) == (1280, 720)


def test_png_size_on_garbage_is_zero_not_a_crash():
    assert desktop._png_size(b"not a png") == (0, 0)
    assert desktop._png_size(b"") == (0, 0)


def test_screenshot_returns_an_image_block(gate, monkeypatch, tmp_path):
    png = _fake_png(800, 600)

    def fake_run(cmd, **kwargs):
        # scrot is invoked as [scrot, "-o", <output path>]
        Path(cmd[2]).write_bytes(png)

        class R:
            returncode = 0
            stderr = b""

        return R()

    # CI has no scrot (and no X server). Stub the binary lookup as well
    # as the call itself -- otherwise the test fails on the environment
    # rather than on the behaviour it is meant to pin.
    monkeypatch.setattr(desktop.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(desktop.subprocess, "run", fake_run)
    shot = desktop.screenshot()
    assert shot["type"] == "image"
    assert shot["mediaType"] == "image/png"
    assert base64.b64decode(shot["data"])[:8] == b"\x89PNG\r\n\x1a\n"
    assert shot["width"] == 800
    assert shot["height"] == 600


def test_screenshot_deletes_its_temp_file(gate, monkeypatch):
    """A screenshot can contain private content; it must not be left in /tmp."""
    png = _fake_png(10, 10)
    captured = {}

    def fake_run(cmd, **kwargs):
        path = Path(cmd[2])
        path.write_bytes(png)
        captured["path"] = path

        class R:
            returncode = 0
            stderr = b""

        return R()

    monkeypatch.setattr(desktop.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(desktop.subprocess, "run", fake_run)
    desktop.screenshot()
    assert not captured["path"].exists()


def test_screenshot_reports_a_missing_scrot_clearly(gate, monkeypatch):
    """A missing capture tool must be an explicit, actionable error --
    not a crash and not a silent empty image."""
    monkeypatch.setattr(desktop.shutil, "which", lambda name: None)
    with pytest.raises(desktop.DesktopUnavailable) as exc:
        desktop.screenshot()
    assert "scrot" in str(exc.value)


# ──────────────────────────────────────────────────────────────────────
# MCP layer
# ──────────────────────────────────────────────────────────────────────


def test_tool_names_match_the_published_recipe():
    """Meta's recipe prompt names these tools explicitly. Renaming them
    breaks the published prompt, which is the whole point of matching."""
    names = {t["name"] for t in mcp.TOOLS}
    assert names == {
        "cua_screenshot",
        "cua_left_click",
        "cua_right_click",
        "cua_move_cursor",
        "cua_type_text",
        "cua_press_key",
        "cua_run_command",
    }


def test_initialize_reports_the_protocol():
    out = mcp._handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert out["result"]["protocolVersion"] == mcp.PROTOCOL_VERSION
    assert out["result"]["serverInfo"]["name"] == "tahuti"


def test_tools_list_returns_every_tool():
    out = mcp._handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert len(out["result"]["tools"]) == 7


def test_tools_call_unknown_tool_is_an_error_not_a_crash():
    out = mcp._handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "cua_nope", "arguments": {}},
        }
    )
    assert out["result"]["isError"] is True
    assert "unknown tool" in out["result"]["content"][0]["text"]


def test_refusal_surfaces_to_the_model_as_a_refusal(gate):
    """A refusal must be reported, not raised -- the model needs to see
    'REFUSED' so it stops retrying instead of looping."""
    gate.verdict = _Verdict.DENY
    out = mcp._handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "cua_left_click", "arguments": {"x": 5, "y": 5}},
        }
    )
    assert out["result"]["isError"] is True
    assert "REFUSED" in out["result"]["content"][0]["text"]


def test_bad_coordinate_argument_is_reported_not_crashed(gate):
    out = mcp._handle(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "cua_left_click", "arguments": {"x": "left", "y": 5}},
        }
    )
    assert out["result"]["isError"] is True
    assert "ERROR" in out["result"]["content"][0]["text"]


def test_unknown_method_is_a_jsonrpc_error():
    out = mcp._handle({"jsonrpc": "2.0", "id": 6, "method": "nope/nope"})
    assert out["error"]["code"] == -32601


def test_notification_gets_no_response():
    assert (
        mcp._handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    )


# ── coordinate mapping: the model answers in image space, xdotool needs pixels ──


def test_screen_point_is_identity_without_a_downscaled_capture(monkeypatch):
    monkeypatch.setattr(desktop, "_CAPTURE_SCALE", 1.0)
    assert desktop._screen_point(750, 15) == (750, 15)


def test_screenshot_records_the_downscale_ratio(gate, monkeypatch):
    """A 1920-wide capture shown to the model as 1280 must record 1.5."""
    raw, shrunk = _fake_png(1920, 1080), _fake_png(1280, 720)

    def fake_run(cmd, **kwargs):
        Path(cmd[2]).write_bytes(raw)

        class R:
            returncode = 0
            stderr = b""

        return R()

    monkeypatch.setattr(desktop.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(desktop.subprocess, "run", fake_run)
    # Pillow is not a declared dependency; pin the ratio without needing it.
    monkeypatch.setattr(desktop, "_downscale_png", lambda data, w: shrunk)

    shot = desktop.screenshot()
    assert shot["width"] == 1280
    assert shot["screen_width"] == 1920
    assert desktop._CAPTURE_SCALE == pytest.approx(1.5)


def test_click_maps_image_coords_back_to_screen_pixels(monkeypatch):
    """The Muse Spark probe answered (750,15) on a 1280x720 image.

    On a 1920x1080 panel the pointer must actually land at (1125,23) --
    before this mapping existed it clicked (750,15), 375px off target.
    """
    monkeypatch.setattr(desktop, "_CAPTURE_SCALE", 1.5)
    monkeypatch.setattr(desktop, "_require_approval", lambda *a, **k: None)
    monkeypatch.setattr(desktop, "_require", lambda name: f"/usr/bin/{name}")
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(list(cmd))

        class R:
            returncode = 0
            stderr = b""

        return R()

    monkeypatch.setattr(desktop.subprocess, "run", fake_run)
    out = desktop.left_click(750, 15)

    moves = [c for c in seen if "mousemove" in c]
    assert moves, "no pointer move was issued"
    # 15 * 1.5 = 22.5 -> round() half-to-even gives 22
    assert moves[0][-2:] == ["1125", "22"]
    assert out["x"] == 1125 and out["y"] == 22


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
