"""Tahuti — real-desktop primitives for computer use on Linux.

Why this exists
---------------
Meta's "Computer use" cookbook recipe drives a **throwaway Cua Docker
sandbox** — their own words: "the model never touches your machine." Their
sibling macOS recipe drives your *real* Mac with native events. There is no
Linux equivalent that drives a real desktop.

That gap IS the Tahuti bridge: Muse Spark (vision + reasoning) driving the
operator's actual Linux desktop. The model's loop is unchanged — look at a
screenshot, decide where to click or type, act, look again. Only the
*substrate* differs: a real X11 desktop instead of a disposable container.

The published prompt works verbatim against this module because the tool
names match Meta's (``cua_screenshot``, ``cua_left_click``, ...).

Safety model
------------
Every action goes through the SHARED approval gate (``master-ai-cli's``
``scripts/approval_gate.py``) using the ``cua:<action>:<mode>`` pattern keys
that already exist in ``master-ai-cli/scripts/computer_use_tool.py`` for
exactly this purpose. The gate **fails closed**: with no human present, a
cron run or a headless API server gets a hard DENY rather than an
unattended click-and-type session on the operator's live desktop.

``mode`` is ``foreground`` or ``background``, and a grant for one does not
cover the other -- a background grant must not silently authorize foreground
input.

Input safety
------------
xdotool fires blind at whatever window is focused. If focus drifts between
aiming and acting -- another window raising, a popover grabbing focus -- the
input lands in the wrong place, silently. Confirmed live on this box: a test
keystroke landed in an unrelated terminal instead of the intended dialog,
with zero error.

So mouse and keyboard actions route through ``focus_guard.py``, which
verifies focus BEFORE and AFTER each injection and raises ``FocusLostError``
instead of guessing. That module is reused as-is, not reimplemented.

Click settle
------------
A raw uinput click fires no mousemove, so browser/Electron UIs recompute
the hover target on the *previous* mousemove. If the cursor stopped moving
before the click, the hover target is stale and the click lands on nothing.
Confirmed live 2026-07-21: a 1px out-and-back mousemove immediately before
the click reliably wakes the target. That is what ``click_with_settle.sh``
does, and it is reused as-is.

Not reimplemented on purpose
----------------------------
Every mechanism here already exists on this box and is proven live:
``focus_guard.py`` (focus-verified injection), ``click_with_settle.sh``
(hover wake), ``ptt_pynput.py`` (real xdotool typing), ``scrot`` (screen
capture). Tahuti is the bridge that exposes them to a vision model -- not a
second implementation of any of them.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

# The approval gate lives in master-ai-cli (the framework AI Controller
# runs on). Import it by path rather than adding a dependency: AI
# Controller is the Linux reference implementation and does not depend on
# master-ai-cli's package layout.
_MASTER_AI_CLI = Path(os.path.expanduser("~/master-ai-cli"))


def _import_from_path(name: str, path: Path):
    """Import a module by file path.

    The module MUST be registered in ``sys.modules`` before it executes.
    A module that defines a dataclass resolves its own annotations through
    ``sys.modules[cls.__module__]`` at class-creation time, so an
    unregistered module raises ``AttributeError: 'NoneType' object has no
    attribute '__dict__'`` the moment it defines one. Registering first
    is the documented requirement for ``importlib.util.module_from_spec``.
    """
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_approval_gate():
    """Import master-ai-cli's shared approval gate.

    IMPORTANT: the module is imported under its CANONICAL name
    (``approval_gate``), not a private one. The gate holds process-wide
    state -- the explicit callback, the session grants, the permanent
    allowlist. Loading it under a different name creates a SECOND
    instance of that state, so a caller that registers an approval
    callback through the normal import path would be talking to a
    different gate than the one this module consults, and every action
    would be refused even though the operator approved it.

    If ``approval_gate`` is already in ``sys.modules`` (the usual case:
    the host imported it first), that exact module is reused.

    Fails closed by design: if the gate cannot be loaded at all, the
    caller must not act on the desktop. A missing gate is a hard error,
    never a licence to click.
    """
    import importlib.util
    import sys

    canonical = "approval_gate"
    existing = sys.modules.get(canonical)
    if existing is not None:
        return existing

    path = _MASTER_AI_CLI / "scripts" / "approval_gate.py"
    if not path.exists():
        raise RuntimeError(
            f"approval gate not found at {path} -- refusing to act on the "
            "desktop without it (fail closed)"
        )
    spec = importlib.util.spec_from_file_location(canonical, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load approval gate from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[canonical] = module
    spec.loader.exec_module(module)
    return module


_APPROVAL = None


def _approval():
    global _APPROVAL
    if _APPROVAL is None:
        _APPROVAL = _load_approval_gate()
    return _APPROVAL


def _load_focus_guard():
    """Import AI Controller's focus-verified xdotool wrapper."""
    path = Path(__file__).resolve().parent / "focus_guard.py"
    return _import_from_path("_tahuti_focus_guard", path)


_FOCUS = None


def _focus():
    global _FOCUS
    if _FOCUS is None:
        _FOCUS = _load_focus_guard()
    return _FOCUS


_ENV = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}


class DesktopActionRefused(Exception):
    """The operator (or policy) refused the action."""


class DesktopUnavailable(Exception):
    """A required tool (scrot, xdotool) is not installed."""


def _require(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        raise DesktopUnavailable(f"{binary} is not installed")
    return path


def _pattern_key(action: str, mode: str) -> str:
    """The approval-gate key for an action.

    ``cua:<action>:<mode>``. A grant for one mode never covers the other:
    a background grant must not silently authorize foreground input.
    """
    if mode not in ("foreground", "background"):
        raise ValueError(f"mode must be foreground or background, got {mode!r}")
    return f"cua:{action}:{mode}"


def _require_approval(action: str, mode: str, description: str) -> None:
    """Ask the shared gate. Raises DesktopActionRefused on DENY/TIMEOUT."""
    gate = _approval()
    verdict = gate.request_approval(
        _pattern_key(action, mode),
        description,
        fail_closed_when_no_human=True,
        action=action,
        mode=mode,
    )
    if verdict == gate.Verdict.DENY:
        raise DesktopActionRefused(f"operator denied: {description}")
    if verdict == gate.Verdict.TIMEOUT:
        raise DesktopActionRefused(f"approval timed out: {description}")


def _active_window() -> str:
    """The currently focused X11 window id, or "" if it cannot be read."""
    win = _focus().active_window()
    return str(win) if win else ""


# ──────────────────────────────────────────────────────────────────────
# Screen
# ──────────────────────────────────────────────────────────────────────


def screenshot(mode: str = "foreground", max_width: int = 1280) -> dict[str, Any]:
    """Capture the screen as a PNG, returned base64-encoded.

    Vision models need the pixels; a path on disk is useless to a remote
    model. The PNG is downscaled if it exceeds ``max_width`` so a
    screenshot fits comfortably in a model's image budget -- the published
    recipe notes a 50-images-per-request API limit, so image weight
    matters.

    A screenshot is a READ, not an action on the machine, but it still
    goes through the gate: it captures whatever is on screen, which may
    include private content. The operator decides.
    """
    _require_approval("screenshot", mode, "Capture the screen")
    scrot = _require("scrot")

    out_dir = Path(tempfile.gettempdir())
    out_path = out_dir / f"tahuti_shot_{os.getpid()}_{int(time.time() * 1000)}.png"
    try:
        result = subprocess.run(
            [scrot, "-o", str(out_path)],
            env=_ENV,
            capture_output=True,
            timeout=15,
        )
        if result.returncode != 0 or not out_path.exists():
            raise DesktopUnavailable(
                f"scrot failed ({result.returncode}): "
                f"{result.stderr.decode(errors='ignore').strip()}"
            )
        data = out_path.read_bytes()
        width, height = _png_size(data)
        if width and width > max_width:
            data = _downscale_png(data, max_width)
            width, height = _png_size(data)
        return {
            "type": "image",
            "mediaType": "image/png",
            "data": base64.b64encode(data).decode("ascii"),
            "width": width,
            "height": height,
        }
    finally:
        # The PNG can contain whatever was on screen. Do not leave it
        # lying around in /tmp.
        try:
            out_path.unlink()
        except OSError:
            pass


def _png_size(data: bytes) -> tuple[int, int]:
    """(width, height) from a PNG's IHDR chunk. (0, 0) if unparseable."""
    # PNG signature (8) + IHDR chunk: length(4) type(4) then width/height
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return 0, 0
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


def _downscale_png(data: bytes, max_width: int) -> bytes:
    """Shrink a PNG to max_width, preserving aspect ratio.

    Uses Pillow when it is importable. Pillow is not a declared dependency
    of AI Controller (it rides along in the venv via another package), so
    this degrades gracefully: if it cannot be imported, the ORIGINAL image
    is returned rather than losing the screenshot entirely. A larger image
    costs the model more tokens but is still a correct image; a missing
    image is a dead loop.
    """
    try:
        import io

        from PIL import Image
    except Exception:
        return data
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.width <= max_width:
                return data
            ratio = max_width / float(img.width)
            new_size = (max_width, max(1, int(round(img.height * ratio))))
            resized = img.resize(new_size, Image.LANCZOS)
            buf = io.BytesIO()
            resized.save(buf, format="PNG")
            return buf.getvalue()
    except Exception:
        return data


# ──────────────────────────────────────────────────────────────────────
# Mouse
# ──────────────────────────────────────────────────────────────────────


def _move(x: int, y: int) -> None:
    _require("xdotool")
    subprocess.run(
        ["xdotool", "mousemove", "--", str(x), str(y)],
        env=_ENV,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
    )


def move_cursor(x: int, y: int, mode: str = "foreground") -> dict[str, Any]:
    """Move the pointer to (x, y) without clicking."""
    _require_approval("move", mode, f"Move cursor to ({x}, {y})")
    _move(x, y)
    return {"ok": True, "action": "move_cursor", "x": x, "y": y}


def click(
    x: int,
    y: int,
    button: int = 1,
    mode: str = "foreground",
) -> dict[str, Any]:
    """Click at (x, y). button 1=left, 2=middle, 3=right.

    Moves the pointer first, then wakes the hover target with a 1px
    out-and-back mousemove before the click (the hover-settle trick --
    see the module docstring), then clicks.
    """
    action = (
        "left_click" if button == 1 else ("right_click" if button == 3 else "click")
    )
    _require_approval(action, mode, f"{action} at ({x}, {y})")
    _require("xdotool")

    _move(x, y)
    time.sleep(0.03)
    # Hover wake: a raw click fires no mousemove, so the app's idea of
    # what is under the pointer can be stale. Force a real mousemove
    # immediately before the click so the target is recomputed.
    subprocess.run(
        ["xdotool", "mousemove_relative", "--", "1", "0"],
        env=_ENV,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
    )
    subprocess.run(
        ["xdotool", "mousemove_relative", "--", "-1", "0"],
        env=_ENV,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
    )
    time.sleep(0.03)
    subprocess.run(
        ["xdotool", "click", str(button)],
        env=_ENV,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
    )
    return {"ok": True, "action": action, "x": x, "y": y, "button": button}


def left_click(x: int, y: int, mode: str = "foreground") -> dict[str, Any]:
    """Left-click at (x, y)."""
    return click(x, y, button=1, mode=mode)


def right_click(x: int, y: int, mode: str = "foreground") -> dict[str, Any]:
    """Right-click at (x, y)."""
    return click(x, y, button=3, mode=mode)


# ──────────────────────────────────────────────────────────────────────
# Keyboard
# ──────────────────────────────────────────────────────────────────────


def type_text(
    text: str,
    mode: str = "foreground",
    delay_ms: int = 0,
) -> dict[str, Any]:
    """Type text into the focused window.

    Routed through focus_guard so a focus drift raises instead of typing
    into the wrong window. ``delay_ms`` slows the injection for apps that
    drop fast synthetic keystrokes.
    """
    _require_approval("type", mode, f"Type {len(text)} character(s)")
    fg = _focus()
    target = _active_window()
    if not target:
        raise DesktopUnavailable("could not determine the focused window")
    try:
        fg.guarded_type(target, text, delay_ms=delay_ms)
    except fg.FocusLostError as e:
        raise DesktopActionRefused(f"focus was lost, refusing to type: {e}") from e
    return {"ok": True, "action": "type_text", "length": len(text)}


def press_key(
    key: str,
    mode: str = "foreground",
) -> dict[str, Any]:
    """Press a named key (Return, Escape, Tab, ctrl+s, ...).

    Named keys are the ones that plausibly summon a new surface, so
    focus_guard waits for the X11 window tree to settle before returning.
    """
    _require_approval("press_key", mode, f"Press key {key!r}")
    fg = _focus()
    target = _active_window()
    if not target:
        raise DesktopUnavailable("could not determine the focused window")
    try:
        fg.guarded_key(target, key)
    except fg.FocusLostError as e:
        raise DesktopActionRefused(
            f"focus was lost, refusing to press {key!r}: {e}"
        ) from e
    return {"ok": True, "action": "press_key", "key": key}


def run_command(command: str, mode: str = "foreground") -> dict[str, Any]:
    """Run a shell command ON THIS MACHINE.

    This is the highest-blast-radius tool in the set -- it is not scoped
    to the desktop the way a click is, it runs wherever the agent does.
    It exists because the published recipe's agent uses it to probe for
    and launch applications (``which gnome-mines``, ``ls /usr/games``),
    and that is how the model discovers an app it was never told the
    location of.
    """
    _require_approval("run_command", mode, f"Run command: {command}")
    result = subprocess.run(
        command,
        shell=True,
        env=_ENV,
        capture_output=True,
        timeout=120,
    )
    return {
        "ok": result.returncode == 0,
        "action": "run_command",
        "exit_code": result.returncode,
        "stdout": result.stdout.decode(errors="ignore")[-8000:],
        "stderr": result.stderr.decode(errors="ignore")[-4000:],
    }
