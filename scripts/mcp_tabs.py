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
import urllib.parse
from pathlib import Path

SERVER_DIR = Path(os.environ.get("SENSEI_MCP_DIR", str(Path.home() / "scripts")))
WAIT_S = 20  # a tab opening is quick; do not hang a voice goal on a dead extension


def _server():
    if str(SERVER_DIR) not in sys.path:
        sys.path.insert(0, str(SERVER_DIR))
    return importlib.import_module("sensei_mcp_server")


# Schemes that mean "already a URI" (round-4 review #6). localhost:8080 and
# notes:2026.md urlparse as "scheme:..." — a colon alone must not hide a
# filesystem path behind a URI return.
_URI_SCHEMES = {"mailto", "tel", "data", "magnet", "about", "chrome", "file",
                "http", "https", "ftp"}


def to_url(target: str | Path) -> str:
    """The target as a URL/URI the bridge understands.

    OCR finding D: detection goes through urllib.parse.urlparse, so URIs that
    carry no scheme separator ("mailto:you@example.com", "tel:911",
    "data:text/plain,hi", "magnet:?xt=...") are returned AS-IS — they are
    URIs, not files; the old `"://" in` check waited for a separator those
    URIs do not have and turned them into file:// nonsense paths. Anything
    scheme-less is a filesystem path. An EMPTY target is an error: the old
    code silently resolved it to file://<cwd>, "opening" the caller's whole
    directory in a browser tab."""
    t = str(target)
    if not t.strip():
        raise ValueError("no target given to open_tab")
    if "://" in t or urllib.parse.urlparse(t).scheme.lower() in _URI_SCHEMES:
        return t
    return Path(t).expanduser().resolve().as_uri()


def _find_tab_created(obj: object) -> dict | None:
    """First `{"tab_created": {"id": ...}}` anywhere inside `obj`, at ANY
    nesting — JSON-RPC result envelopes differ between bridge versions
    (OCR finding C: success must be POSITIVE evidence, not the absence of
    inner["ok"]). `id` must be int-able; anything else (JSON-RPC error, odd
    envelope, tab_created without an id) is not proof and returns None."""
    if isinstance(obj, dict):
        tc = obj.get("tab_created")
        if isinstance(tc, dict):
            try:
                int(tc.get("id"))
            except (TypeError, ValueError):
                pass
            else:
                return tc
        for v in obj.values():
            found = _find_tab_created(v)
            if found is not None:
                return found
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            found = _find_tab_created(v)
            if found is not None:
                return found
    return None


def open_tab(target: str | Path) -> dict:
    url = ""
    try:
        # Inside the try: to_url raises on an empty target (finding D) and a
        # raise must come out as ok=False — never into a voice goal.
        url = to_url(target)
        srv = _server()
        if not srv._bridge_alive():
            return {"ok": False, "reason": "bridge_unreachable", "url": url}
        out = srv._dispatch("BROWSER_TAB_CREATE", {"target": url}, wait=WAIT_S)
    except Exception as exc:  # noqa: BLE001 - report, never raise into a voice goal
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}", "url": url}
    # OCR finding C: ok only on a created tab seen anywhere in the reply.
    created = _find_tab_created(out)
    return {"ok": created is not None, "url": url,
            "detail": created if created is not None else (out or {})}
