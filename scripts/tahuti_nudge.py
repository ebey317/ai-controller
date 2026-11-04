#!/usr/bin/env python3
"""tahuti_nudge.py — the "want to learn more?" avatar reminder.

About every two hours (a systemd user timer runs this) the avatar pops up with
three choices — Open README / Later / Turn off. It stays quiet when you are busy
(talking, a goal running, a card already open, speech playing) and does nothing
at all once you have turned it off.

    python3 tahuti_nudge.py            # maybe show the reminder (what the timer runs)
    python3 tahuti_nudge.py --force    # show it now, ignoring the quiet checks
    python3 tahuti_nudge.py --on|--off # turn the reminder back on / off
    python3 tahuti_nudge.py --open     # open the README
    python3 tahuti_nudge.py --status
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE_DIR = Path.home() / ".tahuti"
OFF_FLAG = STATE_DIR / "readme_nudge_off"
LAST_FILE = STATE_DIR / "readme_nudge_last"
README_HTML = STATE_DIR / "README.html"
CAPTION = Path.home() / ".config" / "ai-controller" / "tahuti_caption.json"
GOALS_PROCESSING = STATE_DIR / "goals" / "processing"
PRESENTER = HERE / "tahuti_choice_presenter.py"

MIN_GAP_S = 100 * 60          # never twice within ~100 minutes, even if the timer fires early
BUSY_CAPTION_AGE_S = 90       # a non-idle caption this fresh means a conversation is going on


def enabled() -> bool:
    return not OFF_FLAG.exists()


def set_enabled(on: bool) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if on:
        OFF_FLAG.unlink(missing_ok=True)
    else:
        OFF_FLAG.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")


def ensure_readme() -> Path:
    """The README.html, built fresh if it is missing."""
    if not README_HTML.exists():
        subprocess.run([sys.executable, str(HERE / "make_tahuti_readme.py")], check=False, timeout=60)
    return README_HTML


def open_readme() -> bool:
    """Open the README as an MCP tab (the tab group that shows an agent opened
    it). Never a bare xdg-open tab. False if the browser bridge is not connected;
    the caller then falls back to showing the guide inside the card.
    TAHUTI_README_OPENER (tests only) swaps in a stand-in so no real tab opens."""
    path = ensure_readme()
    if not path.exists():
        return False
    stand_in = os.environ.get("TAHUTI_README_OPENER")
    if stand_in:
        try:
            subprocess.Popen([stand_in, str(path)], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            return False
    import mcp_tabs
    return bool(mcp_tabs.open_tab(path).get("ok"))


def _busy_reason() -> str:
    """Why now is a bad moment, or '' when it is fine."""
    try:
        if GOALS_PROCESSING.exists() and any(GOALS_PROCESSING.iterdir()):
            return "a goal is running"
    except Exception:
        pass
    try:
        cap = json.loads(CAPTION.read_text(encoding="utf-8"))
        age = time.time() - CAPTION.stat().st_mtime
        if cap.get("state") not in (None, "", "idle") and age < BUSY_CAPTION_AGE_S:
            return f"Tahuti is {cap.get('state')}"
    except Exception:
        pass
    if Path("/tmp/tahuti-pulse.pid").exists():
        try:
            os.kill(int(Path("/tmp/tahuti-pulse.pid").read_text().strip()), 0)
            return "the avatar is already showing"
        except Exception:
            pass
    out = subprocess.run(["pgrep", "-af", "tahuti_choice_presenter"], capture_output=True, text=True).stdout
    if any("tahuti_choice_presenter" in ln and "pgrep" not in ln and str(os.getpid()) != ln.split()[0]
           for ln in out.splitlines()):
        return "a card is already open"
    out = subprocess.run(["pgrep", "-x", "hermes_tts_play"], capture_output=True, text=True).stdout.split()
    if out:
        return "speech is playing"
    return ""


def should_show(now: float | None = None) -> tuple[bool, str]:
    now = now or time.time()
    if not enabled():
        return False, "turned off"
    try:
        if now - float(LAST_FILE.read_text().strip()) < MIN_GAP_S:
            return False, "shown recently"
    except Exception:
        pass
    busy = _busy_reason()
    if busy:
        return False, busy
    return True, ""


def show(force: bool = False) -> dict:
    """Pop the reminder card (the presenter does the drawing and handles the choice)."""
    ok, why = (True, "") if force else should_show()
    if not ok:
        return {"shown": False, "why": why}
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LAST_FILE.write_text(str(time.time()), encoding="utf-8")
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
    proc = subprocess.run([sys.executable, str(PRESENTER), "--nudge"], capture_output=True, text=True,
                          timeout=200, env=env)
    for line in reversed((proc.stdout or "").splitlines()):
        try:
            return {"shown": True, **json.loads(line)}
        except Exception:
            continue
    return {"shown": True, "ok": False, "why": "no result from the card"}


def main(argv: list[str]) -> int:
    if "--on" in argv:
        set_enabled(True)
        print("README reminder: on")
    elif "--off" in argv:
        set_enabled(False)
        print("README reminder: off")
    elif "--open" in argv:
        print("opened" if open_readme() else "could not open the README")
    elif "--status" in argv:
        ok, why = should_show()
        print(f"enabled={enabled()} would_show_now={ok} ({why or 'ok'})")
    else:
        print(json.dumps(show(force="--force" in argv)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
