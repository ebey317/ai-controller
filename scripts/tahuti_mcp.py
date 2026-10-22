#!/usr/bin/env python3
"""Tahuti — MCP server exposing the real Linux desktop to a vision model.

This is the bridge Tahuti exists to build. Meta's published "Computer use"
cookbook recipe drives a **throwaway Cua Docker sandbox** — their own
words: "the model never touches your machine." Their sibling macOS recipe
drives your *real* Mac with native events. There is no Linux equivalent
that drives a real desktop.

This server closes that gap: Muse Spark (vision + reasoning) driving the
operator's actual Linux desktop, through the same
look → act → look loop the recipe describes. Only the substrate differs —
a real X11 desktop instead of a disposable container.

The tool names match Meta's exactly (``cua_screenshot``,
``cua_left_click``, ``cua_right_click``, ``cua_move_cursor``,
``cua_type_text``, ``cua_press_key``, ``cua_run_command``) so the
published prompt works verbatim against this server. That prompt's
"the ONLY way to interact with it is through the cua_* tools" framing is
load-bearing: it stops the agent from reaching for its own host shell and
getting confused about which machine it is on.

Safety
------
Every action goes through master-ai-cli's shared approval gate using the
``cua:<action>:<mode>`` pattern keys, and the gate **fails closed**: with
no human present, an unattended run gets a hard DENY rather than a
click-and-type session on the operator's live desktop. A grant for
``foreground`` never covers ``background``.

Protocol
--------
JSON-RPC 2.0 over stdio, matching the shape of the existing
``scripts/sensei_mcp_server.py`` in this repo's sibling project, so the
two behave the same way to a client.

Usage
-----
Register with OpenCode in ``~/.config/opencode/opencode.json``::

    "mcp": {
      "cua": {
        "type": "local",
        "command": ["/home/elijah/ai-controller/.venv/bin/python",
                    "/home/elijah/ai-controller/scripts/tahuti_mcp.py"],
        "environment": { "TAHUTI_MODE": "foreground" },
        "enabled": true
      }
    }
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tahuti_desktop as desktop  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "tahuti"

# Default mode for every action. "foreground" means the operator can see
# the click land; "background" is for the narrow case where the desktop
# is not being watched. A grant for one never covers the other.
DEFAULT_MODE = os.environ.get("TAHUTI_MODE", "foreground")


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


TOOLS = [
    _tool(
        "cua_screenshot",
        "Capture the screen as a PNG image and return it for viewing. "
        "Call this FIRST, before any other tool, to see what is on screen, "
        "and again after every action to see the result.",
        {},
        [],
    ),
    _tool(
        "cua_left_click",
        "Left-click at the given screen coordinates (x, y). Take the "
        "coordinates from a cua_screenshot.",
        {
            "x": {"type": "integer", "description": "Screen X coordinate"},
            "y": {"type": "integer", "description": "Screen Y coordinate"},
        },
        ["x", "y"],
    ),
    _tool(
        "cua_right_click",
        "Right-click at the given screen coordinates (x, y).",
        {
            "x": {"type": "integer", "description": "Screen X coordinate"},
            "y": {"type": "integer", "description": "Screen Y coordinate"},
        },
        ["x", "y"],
    ),
    _tool(
        "cua_move_cursor",
        "Move the mouse pointer to (x, y) without clicking.",
        {
            "x": {"type": "integer", "description": "Screen X coordinate"},
            "y": {"type": "integer", "description": "Screen Y coordinate"},
        },
        ["x", "y"],
    ),
    _tool(
        "cua_type_text",
        "Type text into the currently focused window.",
        {
            "text": {"type": "string", "description": "Text to type"},
            "delay_ms": {
                "type": "integer",
                "description": "Delay between keystrokes in ms (default 0)",
            },
        },
        ["text"],
    ),
    _tool(
        "cua_press_key",
        "Press a named key, e.g. Return, Escape, Tab, ctrl+s, alt+Tab.",
        {
            "key": {"type": "string", "description": "Key name or combo"},
        },
        ["key"],
    ),
    _tool(
        "cua_run_command",
        "Run a shell command on the machine hosting this desktop. Use it "
        "to find and launch applications (e.g. 'which <app>', "
        "'ls /usr/games').",
        {
            "command": {"type": "string", "description": "Shell command to run"},
        },
        ["command"],
    ),
]


def _text_result(text: str, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    }


def _image_result(shot: dict[str, Any]) -> dict[str, Any]:
    """Render a screenshot as MCP image content.

    The base64 PNG goes in a separate image block rather than being
    embedded in text -- that is how a vision model receives it.
    """
    return {
        "content": [
            {
                "type": "image",
                "mediaType": shot["mediaType"],
                "data": shot["data"],
            }
        ],
        "isError": False,
    }


def _int_arg(args: dict[str, Any], key: str) -> int:
    value = args.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number, got {value!r}")
    return int(value)


def _mode(args: dict[str, Any]) -> str:
    mode = args.get("mode") or DEFAULT_MODE
    if mode not in ("foreground", "background"):
        raise ValueError(f"mode must be foreground or background, got {mode!r}")
    return mode


def _call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run one tool. Returns an MCP tool result."""
    try:
        if name == "cua_screenshot":
            return _image_result(desktop.screenshot(mode=_mode(args)))

        if name == "cua_left_click":
            desktop.left_click(
                _int_arg(args, "x"), _int_arg(args, "y"), mode=_mode(args)
            )
            return _text_result(f"clicked at ({args.get('x')}, {args.get('y')})")

        if name == "cua_right_click":
            desktop.right_click(
                _int_arg(args, "x"), _int_arg(args, "y"), mode=_mode(args)
            )
            return _text_result(f"right-clicked at ({args.get('x')}, {args.get('y')})")

        if name == "cua_move_cursor":
            desktop.move_cursor(
                _int_arg(args, "x"), _int_arg(args, "y"), mode=_mode(args)
            )
            return _text_result(f"moved cursor to ({args.get('x')}, {args.get('y')})")

        if name == "cua_type_text":
            text = args.get("text")
            if not isinstance(text, str):
                raise ValueError("text must be a string")
            delay = args.get("delay_ms") or 0
            desktop.type_text(text, mode=_mode(args), delay_ms=int(delay))
            return _text_result(f"typed {len(text)} character(s)")

        if name == "cua_press_key":
            key = args.get("key")
            if not isinstance(key, str) or not key:
                raise ValueError("key must be a non-empty string")
            desktop.press_key(key, mode=_mode(args))
            return _text_result(f"pressed {key}")

        if name == "cua_run_command":
            command = args.get("command")
            if not isinstance(command, str) or not command:
                raise ValueError("command must be a non-empty string")
            result = desktop.run_command(command, mode=_mode(args))
            body = (
                f"exit_code={result['exit_code']}\n"
                f"stdout:\n{result['stdout']}\n"
                f"stderr:\n{result['stderr']}"
            )
            return _text_result(body, is_error=not result["ok"])

        return _text_result(f"unknown tool: {name}", is_error=True)

    except desktop.DesktopActionRefused as e:
        # A refusal is a legitimate outcome, not a crash. Say so plainly
        # so the model can report it instead of retrying forever.
        return _text_result(f"REFUSED: {e}", is_error=True)
    except desktop.DesktopUnavailable as e:
        return _text_result(f"UNAVAILABLE: {e}", is_error=True)
    except Exception as e:
        return _text_result(f"ERROR: {type(e).__name__}: {e}", is_error=True)


def _send(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _handle(msg: dict[str, Any]) -> dict[str, Any] | None:
    method = msg.get("method")
    mid = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": "0.1.0"},
            },
        }

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": _call(str(name), args),
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}

    return {
        "jsonrpc": "2.0",
        "id": mid,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def main() -> int:
    """Serve JSON-RPC over stdio until stdin closes."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "parse error"},
                }
            )
            continue
        try:
            response = _handle(msg)
        except Exception:
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": msg.get("id"),
                    "error": {
                        "code": -32603,
                        "message": traceback.format_exc(limit=3),
                    },
                }
            )
            continue
        if response is not None:
            _send(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
