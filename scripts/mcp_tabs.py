"""mcp_tabs.py — open browser tabs the way agents are supposed to: as MCP tabs.

Operator rule (2026-11-04): every tab an agent opens goes through the MCP tab
group, so people can SEE an agent opened it. That means Sensei's browser bridge
(the same one the `sensei` MCP server's tab_create uses), never a bare
`xdg-open` / `google-chrome <url>` tab.

    from mcp_tabs import open_tab
    open_tab("https://example.com")  ->  {"ok": True, "tab_id": 123, ...}
    open_tab(path_to_html_file)      ->  file:// URL

Returns ok=False (never opens a bare tab) when the bridge or its browser
extension is not connected; the caller decides what to tell the person.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

SERVER_DIR = Path(os.environ.get("SENSEI_MCP_DIR", str(Path.home() / "scripts")))
WAIT_S = 20  # a tab opening is quick; do not hang a voice goal on a dead extension


def _server():
    if str(SERVER_DIR) not in sys.path:
        sys.path.insert(0, str(SERVER_DIR))
    return importlib.import_module("sensei_mcp_server")


def to_url(target: str | Path) -> str:
    t = str(target)
    if "://" in t:
        return t
    return Path(t).expanduser().resolve().as_uri()


def open_tab(target: str | Path) -> dict:
    url = to_url(target)
    try:
        srv = _server()
        if not srv._bridge_alive():
            return {"ok": False, "reason": "bridge_unreachable", "url": url}
        out = srv._dispatch("BROWSER_TAB_CREATE", {"target": url}, wait=WAIT_S)
    except Exception as exc:  # noqa: BLE001 - report, never raise into a voice goal
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}", "url": url}
    inner = (((out or {}).get("result") or {}).get("result") or {}).get("result") or {}
    ok = bool((out or {}).get("ok")) and inner.get("ok", True) is not False
    return {"ok": ok, "url": url, "detail": inner or out}
