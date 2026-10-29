#!/usr/bin/env python3
"""Tahuti headless goal runner.

Drains ~/.tahuti/goals/, claims each goal by renaming it to .claim,
works it with the Meta Model API (muse-wonder-1.0 / muse-spark-1.1),
and delivers the answer three ways:
  - POST /speak to the local voice bridge
  - ~/.config/ai-controller/last_agent_response.md
  - Hermex push notification

Safety v1: research/write/answer only. Irreversible actions are converted
into approval requests in ~/.tahuti/approvals/ and never executed.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import ai_controller_paths
from ptt_notify import notify_hermex as notify_hermex_bg

LOG_DIR = Path(ai_controller_paths.ai_controller_dir()) / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "tahuti-goal-runner.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)
logger.info("Tahuti goal runner starting — logs at %s", LOG_FILE)

GOALS_DIR = Path.home() / ".tahuti" / "goals"
APPROVALS_DIR = Path.home() / ".tahuti" / "approvals"
DONE_DIR = GOALS_DIR / "done"
FAILED_DIR = GOALS_DIR / "failed"
LAST_RESPONSE_FILE = Path(ai_controller_paths.config_dir()) / "last_agent_response.md"

META_RESPONSES_URL = "https://api.meta.ai/v1/responses"
DEFAULT_MODEL = os.environ.get("TAHUTI_RUNNER_MODEL", "muse-wonder-1.0")
CODE_MODEL = "muse-spark-1.1"
BRIDGE_URL = (os.environ.get("BRIDGE_URL", "http://127.0.0.1:8002")).rstrip("/")
VOICE_BRIDGE_API_KEY = os.environ.get("VOICE_BRIDGE_API_KEY", "")

POLL_INTERVAL = float(os.environ.get("TAHUTI_RUNNER_POLL_INTERVAL", "2"))
MODEL_TIMEOUT = float(os.environ.get("TAHUTI_RUNNER_MODEL_TIMEOUT", "120"))

SAFETY_PROMPT = (
    "You are the Tahuti headless agent (v1). You may only research, write, and answer. "
    "You must NOT run shell commands, operate the desktop, click, type, open apps, "
    "install or uninstall software, start/stop/restart services, shutdown or reboot, "
    "delete or remove files, send messages, or perform any irreversible action. "
    "If a request would require any of those, refuse and explain that it needs Elijah's approval. "
    "Keep replies concise, plain words, and easy to speak aloud. Under 3 sentences."
)

APPROVAL_VERBS = {
    "open", "launch", "start", "run", "execute", "click", "type", "press",
    "install", "uninstall", "restart", "stop", "shutdown", "reboot", "power",
    "delete", "remove", "rm", "kill", "send", "submit", "purchase", "buy",
    "commit", "push", "deploy",
}

CODE_HINTS = {
    "code", "script", "python", "function", "class", "module", "file",
    "write", "build", "fix", "implement", "refactor", "debug",
}

_running = True


def _handle_signal(signum: int, frame: Optional[object]) -> None:
    global _running
    logger.info("Received signal %s, finishing current goal and exiting", signum)
    _running = False


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def _load_meta_key() -> str:
    """Load Meta Model API key from env / config / keychain without logging it."""
    for src in (
        os.environ.get("MODEL_API_KEY", "").strip(),
        ai_controller_paths.load_env().get("MODEL_API_KEY", "").strip(),
        ai_controller_paths.load_env(os.path.expanduser("~/.hermes/.env")).get(
            "MODEL_API_KEY", ""
        ).strip(),
    ):
        if src and not src.startswith("***") and len(src) > 20:
            return src
    keychain = Path.home() / "Desktop" / "Projects" / "keychain" / "master_ai_keys"
    if keychain.exists():
        try:
            for line in keychain.read_text().splitlines():
                if line.strip().startswith("MODEL_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"\'')
                    if key and not key.startswith("***") and len(key) > 20:
                        return key
        except Exception:
            pass
    return ""


META_KEY = _load_meta_key()


def _model_for_transcript(text: str) -> tuple[str, str]:
    """Return (model, reasoning_effort)."""
    lowered = text.lower()
    if any(h in lowered for h in CODE_HINTS):
        return CODE_MODEL, "medium"
    return DEFAULT_MODEL, "minimal"


def _extract_responses_text(data: dict) -> str:
    last = ""
    try:
        for item in data.get("output", []):
            if item.get("type") != "message":
                continue
            for block in item.get("content", []):
                if block.get("type") == "output_text" and block.get("text"):
                    last = block["text"]
    except Exception:
        pass
    return last.strip()


def _call_model(transcript: str) -> tuple[str, str, str]:
    t0 = time.time()
    model, effort = _model_for_transcript(transcript)
    if not META_KEY:
        raise RuntimeError("MODEL_API_KEY not available")
    payload: dict = {
        "model": model,
        "instructions": SAFETY_PROMPT,
        "input": transcript,
        "reasoning": {"effort": effort},
    }
    if "?" in transcript or any(
        w in transcript.lower()
        for w in {"weather", "news", "lookup", "find", "search", "what", "who", "when", "where", "why", "how"}
    ):
        payload["tools"] = [{"type": "web_search"}]
    try:
        with httpx.Client(timeout=MODEL_TIMEOUT) as client:
            r = client.post(
                META_RESPONSES_URL,
                headers={
                    "Authorization": f"Bearer {META_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        raise RuntimeError(f"Meta API request failed: {exc}") from exc
    text = _extract_responses_text(data)
    if not text:
        text = "I thought about it, but didn't have a reply."
    elapsed = f"{time.time() - t0:.1f}s"
    return text, model, elapsed


def _post_speak(text: str) -> None:
    if not text:
        return
    spoken = text[:500]
    data = urllib.parse.urlencode({"text": spoken}).encode("utf-8")
    headers: dict = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    if VOICE_BRIDGE_API_KEY:
        headers["x-api-key"] = VOICE_BRIDGE_API_KEY
    try:
        req = urllib.request.Request(
            f"{BRIDGE_URL}/speak", data=data, headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as exc:
        logger.warning("POST /speak failed: %s", exc)


def _write_last_response(
    transcript: str, worker: str, response: str, model: Optional[str] = None
) -> None:
    ai_controller_paths.ensure_config_dir()
    model_tag = f" ({model})" if model else ""
    lines = [
        "# Last agent response\n",
        "",
        f"**When:** {datetime.now(timezone.utc).isoformat()}",
        f"**Task:** {transcript}",
        f"**Worker:** {worker}{model_tag}",
        "",
        response,
        "",
    ]
    try:
        LAST_RESPONSE_FILE.write_text("\n".join(lines), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to write last_agent_response.md: %s", exc)


def _notify(title: str, body: str, urgency: str = "normal", sound: bool = False) -> None:
    try:
        notify_hermex_bg(title, body, urgency=urgency, sound=sound)
    except Exception as exc:
        logger.warning("Hermex notify failed: %s", exc)


def _archive(
    path: Path,
    goal_id: str,
    status: str,
    response: str = "",
    error: str = "",
    model: str = "",
) -> None:
    dest_dir = DONE_DIR if status == "done" else FAILED_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{goal_id}.json"
    record = {
        "id": goal_id,
        "status": status,
        "response": response,
        "error": error,
        "model": model,
        "archived_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        tmp = dest.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, dest)
        os.unlink(path)
    except Exception as exc:
        logger.error("Failed to archive %s: %s", path, exc)


def _needs_approval(transcript: str) -> bool:
    words = set(re.findall(r"[a-z]+", transcript.lower()))
    return bool(words & APPROVAL_VERBS)


def _write_approval_request(goal: dict) -> None:
    APPROVALS_DIR.mkdir(parents=True, exist_ok=True)
    path = APPROVALS_DIR / f"{goal['id']}.pending.json"
    record = {
        "id": goal["id"],
        "transcript": goal.get("transcript", ""),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "reason": "irreversible or desktop/shell action requested",
    }
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def _process_goal(path: Path) -> None:
    claim_path = path.with_suffix(".claim")
    try:
        os.replace(path, claim_path)
    except OSError as exc:
        logger.debug("Could not claim %s: %s", path, exc)
        return

    try:
        with open(claim_path, "r", encoding="utf-8") as f:
            goal = json.load(f)
    except Exception as exc:
        logger.error("Invalid goal file %s: %s", claim_path, exc)
        _archive(claim_path, path.stem, "failed", error=f"invalid json: {exc}")
        return

    goal_id = goal.get("id") or path.stem
    transcript = (goal.get("transcript") or "").strip()
    if not transcript:
        logger.error("Goal %s missing transcript", goal_id)
        _archive(claim_path, goal_id, "failed", error="missing transcript")
        return

    if (DONE_DIR / f"{goal_id}.json").exists() or (FAILED_DIR / f"{goal_id}.json").exists():
        logger.info("Goal %s already processed; archiving duplicate", goal_id)
        _archive(claim_path, goal_id, "failed", error="duplicate goal id")
        return

    logger.info("Processing goal %s: %r", goal_id, transcript[:120])

    approval = _needs_approval(transcript)
    response = ""
    worker = ""
    model: Optional[str] = None

    if approval:
        _write_approval_request(goal)
        response = "That request needs your approval before I can act. I've queued it for you."
        worker = "tahuti-runner-v1 (approval gate)"
    else:
        try:
            response, model, elapsed = _call_model(transcript)
            worker = f"tahuti-runner-v1 ({elapsed})"
        except Exception as exc:
            logger.exception("Model call failed for goal %s", goal_id)
            response = f"I ran into a problem: {exc}"
            worker = "tahuti-runner-v1 (model error)"

    _post_speak(response)
    _write_last_response(transcript, worker, response, model=model)
    _notify(
        "Goal needs approval" if approval else "Goal done",
        transcript[:120],
        urgency="normal",
        sound=not approval,
    )

    status = "done" if (approval or model) else "failed"
    _archive(
        claim_path,
        goal_id,
        status,
        response=response,
        error="" if (approval or model) else "model call failed",
        model=model or ("approval-gate" if approval else "unknown"),
    )


def _sleep_or_stop(seconds: float) -> None:
    deadline = time.time() + seconds
    while _running and time.time() < deadline:
        time.sleep(min(0.2, deadline - time.time()))


def main() -> int:
    GOALS_DIR.mkdir(parents=True, exist_ok=True)
    DONE_DIR.mkdir(parents=True, exist_ok=True)
    FAILED_DIR.mkdir(parents=True, exist_ok=True)
    APPROVALS_DIR.mkdir(parents=True, exist_ok=True)

    logger.info("Watching %s for goals", GOALS_DIR)

    while _running:
        try:
            goals = sorted(GOALS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
            for path in goals:
                if not _running:
                    break
                _process_goal(path)
        except Exception as exc:
            logger.exception("Runner loop error: %s", exc)
        _sleep_or_stop(POLL_INTERVAL)

    logger.info("Tahuti goal runner exiting")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
