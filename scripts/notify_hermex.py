#!/usr/bin/env python3
"""Send a push notification to Hermex through the local hermes-webui.

This is a thin client for the POST /api/notify endpoint added to
hermes-webui. It is meant to be called from ai-controller scripts,
master-ai-cli hooks, cron jobs, or any local agent that needs to surface
a status ping on Elijah's iPhone.

Environment:
    HERMES_WEBUI_URL   Base URL of the WebUI (default: http://127.0.0.1:8787)
    HERMES_SESSION_ID  Target Hermes session id (default: tries to read
                       ~/.config/ai-controller/hermes_session_id)

Usage:
    notify_hermex.py --title "STT ready" --body "Long dictation finished."
    notify_hermex.py --title "Build done" --body "Tests passed." --urgency high --sound
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional


def _default_session_id() -> str:
    # ai-controller can write the active Hermes session id here so scripts
    # don't need to pass it every time.
    candidates = [
        os.environ.get("HERMES_SESSION_ID", ""),
        Path("~/.config/ai-controller/hermes_session_id").expanduser().read_text().strip() if Path("~/.config/ai-controller/hermes_session_id").expanduser().exists() else "",
    ]
    for c in candidates:
        if c:
            return c
    return ""


def notify_hermex(
    title: str,
    body: str,
    session_id: Optional[str] = None,
    urgency: str = "normal",
    action: str = "",
    sound: bool = False,
    base_url: Optional[str] = None,
) -> dict:
    """POST a notification to hermes-webui /api/notify.

    Returns the JSON response from the server (includes 'delivered' count).
    Raises on HTTP errors or network failure.
    """
    if not title and not body:
        raise ValueError("title or body is required")

    base = (base_url or os.environ.get("HERMES_WEBUI_URL", "http://127.0.0.1:8787")).rstrip("/")
    sid = (session_id or os.environ.get("HERMES_SESSION_ID", "*")).strip()
    if not sid:
        sid = "*"

    payload = {
        "session_id": sid,
        "title": title,
        "body": body,
        "urgency": urgency,
        "action": action,
        "sound": bool(sound),
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/api/notify",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Push a notification to Hermex")
    parser.add_argument("--title", required=True, help="Notification heading")
    parser.add_argument("--body", default="", help="Notification message")
    parser.add_argument("--session-id", default=None, help="Hermes session id")
    parser.add_argument("--urgency", default="normal", choices=["low", "normal", "high"])
    parser.add_argument("--action", default="", help="Route/path to open on tap")
    parser.add_argument("--sound", action="store_true", help="Play alert sound")
    parser.add_argument("--base-url", default=None, help="hermes-webui base URL")
    args = parser.parse_args()

    try:
        result = notify_hermex(
            title=args.title,
            body=args.body,
            session_id=args.session_id,
            urgency=args.urgency,
            action=args.action,
            sound=args.sound,
            base_url=args.base_url,
        )
        print(json.dumps(result))
        return 0
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="ignore")
        print(f"HTTP {exc.code}: {text}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"notify failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
