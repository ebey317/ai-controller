#!/usr/bin/env python3
"""Tahuti headless goal runner.

Drains ~/.tahuti/goals/, claims each goal by renaming it to .claim,
works it with the Meta Model API (muse-wonder-1.0 / muse-spark-1.1),
and delivers the answer three ways:
  - POST /speak to the local voice bridge
  - ~/.config/ai-controller/last_agent_response.md
  - Hermex push notification

Safety v1: research/write/answer/read only, PLUS opening URLs and a small
allowlist of apps through gated tools (open_url/open_app). The model decides
per task which tool fits; the harness validates every call. Irreversible
actions are converted into approval requests in ~/.tahuti/approvals/ and
never executed. File writes are also queued as approval requests.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import ai_controller_paths
from ptt_notify import notify_hermex as notify_hermex_bg
import voice_goal_schema as vgs  # noqa: E402

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
GRANTS_FILE = Path.home() / ".tahuti" / "grants.json"
DONE_DIR = GOALS_DIR / "done"
FAILED_DIR = GOALS_DIR / "failed"
LAST_RESPONSE_FILE = Path(ai_controller_paths.config_dir()) / "last_agent_response.md"
CAPTION_FILE = Path(ai_controller_paths.config_dir()) / "tahuti_caption.json"
# Persistent conversation memory (2026-11-01): voice replies used to fork
# between a real Tahuti goal and a separate, stateless inline chat answer
# in voice_bridge.py -- neither remembered what was said a turn ago, and
# now that voice_bridge routes everything through here as one path, a
# rolling history is what actually gives it continuity. Verified live
# against the real API: the Responses "input" field accepts a list of
# {"role", "content"} turns in place of a bare string, and the model
# correctly uses injected history as context.
CONVERSATION_HISTORY_FILE = Path.home() / ".tahuti" / "conversation_history.json"
CONVERSATION_HISTORY_MAX_TURNS = int(os.environ.get("TAHUTI_HISTORY_MAX_TURNS", "10"))


def _write_caption(text: str, state: str) -> None:
    """Avatar prompt box feed (2026-10-31): publish run state for display."""
    try:
        CAPTION_FILE.write_text(
            json.dumps({"text": text[:300], "state": state, "time": time.time()}),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("caption write failed: %s", exc)

META_RESPONSES_URL = "https://api.meta.ai/v1/responses"
DEFAULT_MODEL = os.environ.get("TAHUTI_RUNNER_MODEL", "muse-wonder-1.0")
CODE_MODEL = "muse-spark-1.1"
FAST_CHAT_MODEL = os.environ.get("TAHUTI_FAST_CHAT_MODEL", "muse-spark-1.1")
FAST_LOOKUP_MODEL = os.environ.get("TAHUTI_FAST_LOOKUP_MODEL", "muse-spark-1.2")
FAST_CHAT_MAX_TOKENS = int(os.environ.get("TAHUTI_FAST_CHAT_MAX_TOKENS", "256"))
# 512 starved on anything needing a second tool round (open_url then a
# web_search to verify/summarize): the model spent the whole budget on
# reasoning + the search calls and never reached the final text, coming
# back as "I thought about it, but didn't have a reply." -- observed and
# fixed 2026-11-01 alongside the open_url hallucination bug above. 1024
# verified live to leave room for both rounds plus real answer text.
FAST_LOOKUP_MAX_TOKENS = int(os.environ.get("TAHUTI_FAST_LOOKUP_MAX_TOKENS", "1024"))
FAST_EFFORT = "minimal"
BRIDGE_URL = (os.environ.get("BRIDGE_URL", "http://127.0.0.1:8002")).rstrip("/")

SIMPLE_LOOKUP_TRIGGERS = {
    "what", "who", "when", "where", "why", "how",
    "weather", "news", "time", "date", "define", "meaning",
    "lookup", "search", "find"
}
MULTISTEP_HINTS = {
    "then", "after that", "step by step", "first", "second", "third",
    "plan", "research", "analyze", "compare", "summarize",
    "report", "essay", "build", "create file", "write a file"
}
CHAT_SHORT_WORDS = {"hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "yes", "no", "bye", "sup"}
VOICE_BRIDGE_API_KEY = os.environ.get("VOICE_BRIDGE_API_KEY", "")

POLL_INTERVAL = float(os.environ.get("TAHUTI_RUNNER_POLL_INTERVAL", "2"))
MODEL_TIMEOUT = float(os.environ.get("TAHUTI_RUNNER_MODEL_TIMEOUT", "120"))

SAFETY_PROMPT = (
    "You are Tahuti, the headless agent on the operator's Linux desktop — "
    "a model with live tools, and you know your framework. YOUR TOOLS: "
    "open_url (open any website now), open_app (chrome, thunderbird, "
    "terminal, files, code — the coding agent), check_email (read-only "
    "inbox check), read_file and list_dir (home directory only), "
    "write_file (queues an approval request, never writes directly), "
    "web_search (offered on lookup tasks). Decide per task which tool fits "
    "and act immediately on safe, reversible ones — no approval needed. "
    "YOUR HARNESS: controller L+R and the keyboard AGENT toggle file goals "
    "to ~/.tahuti/goals; anything you cannot do waits in ~/.tahuti/approvals "
    "for the operator; your final message is spoken aloud and saved. CLAF "
    "(tiered model router) and the Hermes gateway also live on this machine, "
    "but you cannot call them — never claim otherwise. "
    "The native coding agent is Master AI Sensei — always name it exactly "
    "that, never Claude or another agent. "
    "HARD NOS: no shell or terminal, no clicking or typing, no services, "
    "no installs, no deletes, no sending or buying. Route those to approval "
    "with a one-line reason. Replies concise, plain spoken words, under 3 "
    "sentences."
)

APPROVAL_VERBS = {
    "start", "run", "execute", "click", "type", "press",
    "install", "uninstall", "restart", "stop", "shutdown", "reboot", "power",
    "delete", "remove", "rm", "kill", "send", "submit", "purchase", "buy",
    "commit", "push", "deploy",
}
# NOTE 2026-10-30: "open"/"launch" left the gate — open_url/open_app tools
# below execute those safely, so gating them only produced "needs approval"
# dead ends. The model decides; the executors validate.

CODE_HINTS = {
    "code", "script", "python", "function", "class", "module", "file",
    "write", "build", "fix", "implement", "refactor", "debug", "read", "list",
}

FILE_TOOLS = [
    {
        "type": "function",
        "name": "read_file",
        "description": (
            "Read a text file at an absolute path and return its contents. "
            "Use this to inspect code, logs, notes, or documents. "
            "Only paths inside the user's home directory are allowed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute file path"},
                "offset": {
                    "type": "integer",
                    "description": "1-indexed line to start from (default 1)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read (default all)",
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "list_dir",
        "description": (
            "List the entries in a directory at an absolute path. "
            "Use this to explore the workspace. "
            "Only paths inside the user's home directory are allowed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute directory path"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "write_file",
        "description": (
            "Propose writing content to a file. This tool does NOT write immediately; "
            "it creates an approval request in ~/.tahuti/approvals/ for the operator to review. "
            "Use this whenever the user asks to create, edit, or overwrite a file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute file path"},
                "content": {"type": "string", "description": "Full proposed file content"},
                "reason": {"type": "string", "description": "Why this write is needed"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "open_url",
        "description": (
            "Open a web URL in the default browser NOW (live, no approval needed). "
            "Use this whenever the operator asks to open a website, link, or anything on "
            "the web. Plain domain names work — https:// is added automatically."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL or domain to open"},
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "open_app",
        "description": (
            "Launch an app NOW (live, no approval needed). Allowed names: "
            "chrome (web browser), thunderbird (email), terminal, files (file manager), "
            "code (the coding agent: terminal with Claude). "
            "Anything else is refused by the harness — say it needs approval."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "App name: chrome, thunderbird, terminal, files, or code"},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
]

# Pulled out of FILE_TOOLS for reuse: the fast-chat/fast-lookup lanes strip
# FILE_TOOLS down to almost nothing for latency, but open_url/open_app are
# cheap single-shot calls, not the multi-step file browsing those lanes are
# actually too thin for -- so they get added back in when the transcript
# asks to open something (see OPEN_TRIGGER_WORDS / _call_model below), the
# same accommodation EMAIL_TOOL already gets.
#
# Real bug this fixes (observed 2026-11-01): a fast-lookup goal asking to
# "open up a website" had NO open_url tool in its tools list at all, yet
# the model answered "Opened a beginner-friendly site for you... The page
# is now open in your browser" with a fabricated page title -- a clean
# hallucinated tool-use success, not a hedge. It kept the story going
# ("I open sites with my open_url tool...") until asked a third time,
# then admitted "I didn't open a site yet in this chat." The fix is
# giving the tool back, not teaching the model to hedge better.
OPEN_TOOLS = [t for t in FILE_TOOLS if t["name"] in ("open_url", "open_app")]

# check_email lives outside FILE_TOOLS and is only offered to the model when
# the transcript itself plausibly asks about email (see _all_tools below).
# It used to be unconditionally available on every single goal-take, with a
# description telling the model to "use this whenever email, mail, or inbox
# is asked about" -- with the tool always present and STT noise routinely
# garbling transcripts in this pipeline (confirmed repeatedly: "the medikey
# works" for "the meta key works", etc.), the model had both the standing
# invitation and the opportunity to fire it on loosely-related or misheard
# text. Observed 2026-10-31 as "checking emails randomly." Gating on keyword
# presence first, before the model ever sees the tool, removes that surface
# instead of trying to out-word a tool description.
EMAIL_TOOL = {
    "type": "function",
    "name": "check_email",
    "description": (
        "Check the operator's email NOW (live, read-only, no approval needed). "
        "Returns the recent message list (UID, date, from, subject). Only call "
        "this when the operator has clearly and specifically asked to check, "
        "read, or review their email/inbox -- not on an incidental or ambiguous "
        "mention. Summarize the result in the reply. Cannot send, delete, or "
        "fetch bodies beyond headers."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "days": {"type": "integer", "description": "Look back N days (1-7, default 2)"},
            "unread": {"type": "boolean", "description": "Unread only (default true)"},
        },
        "additionalProperties": False,
    },
}

EMAIL_TRIGGER_WORDS = {"email", "emails", "mail", "inbox", "gmail", "aol", "outlook"}

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
    # Master AI keychains: home KV file first (canonical), then Desktop copy.
    for keychain in (
        Path.home() / ".master_ai_keys",
        Path.home() / "Desktop" / "Projects" / "keychain" / "master_ai_keys",
    ):
        if not keychain.exists():
            continue
        try:
            for line in keychain.read_text(errors="replace").splitlines():
                if line.strip().startswith("MODEL_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"\'')
                    if key and not key.startswith("***") and len(key) > 20:
                        return key
        except Exception:
            pass
    return ""


META_KEY = _load_meta_key()

# Free fallback chain (2026-10-30): fetch/retrieval/small stuff rides free
# keys; cron/background prefers OpenRouter/Nvidia free models. Meta is the
# primary worker; when it fails (observed: Responses 503 while chat stayed
# green), the runner degrades to an OpenRouter :free model instead of
# erroring out loud. (Nvidia direct was tried first — its key 404s on chat
# for this account.) Tools don't exist on the fallback path — direct
# answers only.
FALLBACK_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
FALLBACK_MODEL = os.environ.get(
    "TAHUTI_FALLBACK_MODEL", "nvidia/nemotron-3.5-lightning:free")


def _load_openrouter_key() -> str:
    for src in (
        os.environ.get("OPENROUTER_API_KEY", "").strip(),
        ai_controller_paths.load_env().get("OPENROUTER_API_KEY", "").strip(),
    ):
        if src and not src.startswith("***") and len(src) > 20:
            return src
    for keychain in (
        Path.home() / ".master_ai_keys",
        Path.home() / "Desktop" / "Projects" / "keychain" / "master_ai_keys",
    ):
        if not keychain.exists():
            continue
        try:
            for line in keychain.read_text(errors="replace").splitlines():
                if line.strip().startswith("OPENROUTER_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"\'')
                    if key and not key.startswith("***") and len(key) > 20:
                        return key
        except Exception:
            pass
    return ""


def _contains_hint(lowered: str, word_set: set[str], hints: set[str]) -> bool:
    """Word-boundary-safe hint match.

    Plain `h in lowered` matches a single-word hint as a SUBSTRING, so
    "read" matches inside "reading", "no" inside "know", "how" inside
    "however" -- a real bug observed 2026-11-01: a plain research request
    ("...reading my repos and documents...") got misrouted to the
    reduced-capability code-fast lane purely because it contains "read",
    then came back empty because that lane's token/effort budget is too
    small for an open-ended task. Single-word hints now require a whole-
    word match against word_set; multi-word phrases ("step by step",
    "thank you") keep substring matching -- a full phrase colliding with
    unrelated text is negligible risk, unlike a single common word.
    """
    for h in hints:
        if " " in h:
            if h in lowered:
                return True
        elif h in word_set:
            return True
    return False


def _is_simple_route(text: str) -> str | None:
    """Classify simple goals for fast path: 'chat', 'lookup', or None."""
    t = (text or "").strip()
    if not t:
        return None
    lowered = t.lower()
    words = lowered.split()
    word_set = set(re.findall(r"[a-z']+", lowered))
    if len(t) > 450 or len(words) > 50:
        return None
    # multi-step signals -> not simple unless very short
    if _contains_hint(lowered, word_set, MULTISTEP_HINTS) and len(words) > 14:
        return None
    if _needs_approval(t):
        return None
    # code already handled before calling this
    if _contains_hint(lowered, word_set, CODE_HINTS):
        return None
    # lookup: question or lookup trigger
    if "?" in t or _contains_hint(lowered, word_set, SIMPLE_LOOKUP_TRIGGERS):
        # short lookup
        if len(words) <= 32:
            return "lookup"
    # short chat
    if len(words) <= 10:
        return "chat"
    if len(words) <= 20 and _contains_hint(lowered, word_set, CHAT_SHORT_WORDS):
        return "chat"
    return None


def _model_for_transcript(text: str) -> tuple[str, str, int | None, str]:
    """Return (model, reasoning_effort, max_output_tokens, route)."""
    lowered = text.lower()
    word_set = set(re.findall(r"[a-z']+", lowered))
    if _contains_hint(lowered, word_set, CODE_HINTS):
        # code still fast but needs a bit more tokens
        return CODE_MODEL, "low", 1024, "code-fast"
    simple = _is_simple_route(text)
    if simple == "chat":
        return FAST_CHAT_MODEL, FAST_EFFORT, FAST_CHAT_MAX_TOKENS, "fast-chat"
    if simple == "lookup":
        return FAST_LOOKUP_MODEL, FAST_EFFORT, FAST_LOOKUP_MAX_TOKENS, "fast-lookup"
    return DEFAULT_MODEL, "medium", None, "wonder-multi"


# Selective thinking (Master AI's level vocabulary). Meta rejects "none",
# so it maps to minimal. Runner honors goal["effort"] when valid.
EFFORT_MAP = {"none": "minimal", "low": "low", "medium": "medium",
              "high": "high", "xhigh": "xhigh"}


def _effort_for_goal(goal: dict, transcript: str) -> str:
    effort = EFFORT_MAP.get((goal.get("effort") or "").strip().lower(), "")
    if effort:
        return effort
    return _model_for_transcript(transcript)[1]


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


def _is_under_home(path: Path) -> bool:
    """Return True if path resolves to a location inside the user's home."""
    try:
        resolved = path.resolve()
        home = Path.home().resolve()
        return resolved == home or home in resolved.parents
    except (OSError, ValueError):
        return False


_EMAIL_TRIGGER_RE = re.compile(
    r"\b(" + "|".join(EMAIL_TRIGGER_WORDS) + r")\b", re.IGNORECASE
)

OPEN_TRIGGER_WORDS = {
    "open", "launch", "pull up", "go to", "navigate to", "visit", "bring up",
}
_OPEN_TRIGGER_RE = re.compile(
    r"\b(" + "|".join(OPEN_TRIGGER_WORDS) + r")\b", re.IGNORECASE
)


def _tools_with_live_open_app(tools: list[dict]) -> list[dict]:
    """Rewrite open_app's description with the operator's actual current
    pin names instead of the hardcoded 5-app list. Without this the model
    is told pins don't exist ("anything else is refused by the harness")
    even though _open_app_tool already handles them -- observed 2026-10-31
    as the model never even attempting "open hermes"/"open claude"/etc.
    Replaces the dict rather than mutating FILE_TOOLS' shared object."""
    pins = _reachable_pin_names()
    pin_note = f" Also recognized pins: {', '.join(pins)}." if pins else ""
    out = []
    for tool in tools:
        if tool.get("name") == "open_app":
            tool = dict(tool)
            tool["description"] = (
                "Launch an app NOW (live, no approval needed). Allowed names: "
                "chrome (web browser), thunderbird (email), terminal, files (file manager), "
                "code (Master AI Sensei, a local terminal agent)."
                + pin_note +
                " Anything else is refused by the harness — say it needs approval."
            )
        out.append(tool)
    return out


def _all_tools(transcript: str) -> list[dict]:
    """Tools offered for this transcript: file tools plus optional web search
    and, only when the transcript itself plausibly mentions email, check_email."""
    tools: list[dict] = _tools_with_live_open_app(FILE_TOOLS)
    lowered = transcript.lower()
    if "?" in transcript or any(
        w in lowered
        for w in {"weather", "news", "lookup", "find", "search", "what", "who", "when", "where", "why", "how"}
    ):
        tools.append({"type": "web_search"})
    if _EMAIL_TRIGGER_RE.search(transcript):
        tools.append(EMAIL_TOOL)
    return tools


def _read_file_tool(path: str, offset: int = 1, limit: Optional[int] = None) -> dict:
    p = Path(path)
    if not p.is_absolute():
        return {"ok": False, "error": "path must be absolute"}
    if not _is_under_home(p):
        return {"ok": False, "error": "path outside home directory refused"}
    try:
        resolved = p.resolve()
        if resolved.is_symlink() and not _is_under_home(resolved):
            return {"ok": False, "error": "symlink escapes home directory"}
        if not resolved.is_file():
            return {"ok": False, "error": f"not a file: {path}"}
        text = resolved.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        start = max(0, offset - 1)
        end = len(lines) if limit is None else start + limit
        selected = lines[start:end]
        return {
            "ok": True,
            "path": str(resolved),
            "total_lines": len(lines),
            "content": "\n".join(selected),
        }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _list_dir_tool(path: str) -> dict:
    p = Path(path)
    if not p.is_absolute():
        return {"ok": False, "error": "path must be absolute"}
    if not _is_under_home(p):
        return {"ok": False, "error": "path outside home directory refused"}
    try:
        resolved = p.resolve()
        if not resolved.is_dir():
            return {"ok": False, "error": f"not a directory: {path}"}
        entries = []
        for entry in sorted(resolved.iterdir()):
            try:
                kind = "dir" if entry.is_dir() else "file"
                size = entry.stat().st_size if entry.is_file() else 0
                entries.append({"name": entry.name, "type": kind, "size": size})
            except OSError:
                entries.append({"name": entry.name, "type": "unknown", "size": 0})
        return {"ok": True, "path": str(resolved), "entries": entries}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _write_file_tool(
    goal_id: str, path: str, content: str, reason: str = ""
) -> dict:
    p = Path(path)
    if not p.is_absolute():
        return {"ok": False, "error": "path must be absolute"}
    if not _is_under_home(p):
        return {"ok": False, "error": "path outside home directory refused"}
    try:
        resolved = p.resolve()
        # Granted writes execute for real (Master AI ALWAYS semantics).
        # Everything else queues for the box.
        if _is_granted("tahuti:write_file:background"):
            resolved.parent.mkdir(parents=True, exist_ok=True)
            tmp = resolved.with_name(resolved.name + ".tmp")
            tmp.write_text(content, encoding="utf-8")
            os.replace(tmp, resolved)
            logger.info("granted write executed: %s", resolved)
            return {"ok": True, "written": str(resolved)}
        APPROVALS_DIR.mkdir(parents=True, exist_ok=True)
        import uuid
        safe_name = re.sub(r"[^\w.-]", "_", resolved.name) or "file"
        approval_path = APPROVALS_DIR / f"{goal_id}_{safe_name}_{uuid.uuid4().hex}.pending.json"
        record = {
            "id": goal_id,
            "tool": "write_file",
            "pattern_key": "tahuti:write_file:background",
            "path": str(resolved),
            "content": content,
            "reason": reason or "file write requested",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
        }
        tmp = approval_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, approval_path)
        # One-box law (2026-10-31): the retired standalone worker
        # (tahuti_approval_ui.py) must not respawn. The request is
        # presented in the avatar's own box via _present_approval; the
        # record below remains the audit trail. Only when no box can
        # answer (headless) does the receipt wait below apply, so the
        # model cannot mistake queued for completed.
        result_path = approval_path.with_name(approval_path.name.replace('.pending.json', '.resolved.json'))
        outcome = _present_approval(
            "tahuti:write_file:background",
            f"Write {resolved}",
            why=reason or "file write requested",
        )
        if outcome in ("once", "session"):
            if outcome == "session":
                _save_grant("tahuti:write_file:background")
            resolved.parent.mkdir(parents=True, exist_ok=True)
            wtmp = resolved.with_name(resolved.name + ".tmp")
            wtmp.write_text(content, encoding="utf-8")
            os.replace(wtmp, resolved)
            result_path.write_text(
                json.dumps({"status": "executed", "decision": outcome,
                            "path": str(resolved)}) + "\n",
                encoding="utf-8",
            )
            logger.info("box-approved write executed: %s", resolved)
            return {"ok": True, "status": "executed", "decision": outcome,
                    "path": str(resolved), "receipt": str(result_path)}
        if outcome == "deny":
            result_path.write_text(
                json.dumps({"status": "denied", "decision": "deny",
                            "path": str(resolved)}) + "\n",
                encoding="utf-8",
            )
            return {"ok": False, "status": "denied",
                    "error": "Operator denied the write."}
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            if result_path.exists():
                result = json.loads(result_path.read_text())
                return {'ok': result.get('status') == 'executed',
                        'status': result.get('status'),
                        'decision': result.get('decision'),
                        'path': str(resolved),
                        'error': result.get('error', ''),
                        'receipt': str(result_path)}
            time.sleep(0.2)
        # Cancel an unanswered request rather than let it execute later.
        approval_path.unlink(missing_ok=True)
        return {'ok': False, 'status': 'timeout', 'error': 'Approval timed out; not authorized.'}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _open_url_tool(url: str) -> dict:
    """Open an http(s) URL via xdg-open. Fire-and-forget; never blocks."""
    raw = (url or "").strip()
    if not raw:
        return {"ok": False, "error": "empty url"}
    if len(raw) > 2048:
        return {"ok": False, "error": "url too long (2048 max)"}
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urllib.parse.urlparse(raw)
    except Exception as exc:
        return {"ok": False, "error": f"bad url: {exc}"}
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host or "." not in host:
        return {"ok": False, "error": f"refused (http/https only): {url}"}
    # Hardening (Hermes review 8dda64e): reject userinfo smuggling
    # (https://evil@bank.com) and local/metadata targets — opening local
    # services in the browser is outside what voice commands should reach.
    if "@" in (parts.netloc or ""):
        return {"ok": False, "error": "refused (userinfo in url)"}
    if host in ("localhost",) or host.startswith(("127.", "169.254.", "[::1]")) or host == "::1":
        return {"ok": False, "error": f"refused (local address): {url}"}
    try:
        subprocess.Popen(
            ["xdg-open", raw],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {"ok": True, "opened": raw}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


APP_COMMANDS = {
    "chrome": [["google-chrome"], ["chromium"], ["chromium-browser"]],
    "thunderbird": [["thunderbird"]],
    "terminal": [["gnome-terminal"]],
    "files": [["nautilus"], ["xdg-open", os.path.expanduser("~")]],
    # Native coding agent (shippable: symlink + repo, no personal pins):
    # Master AI Sensei, interactive, in its own terminal.
    "code": [["gnome-terminal", "--", "python3",
              os.path.expanduser("~/scripts/master_ai.py")]],
}

APP_ALIASES = {
    "coding agent": "code",
    "coding-agent": "code",
    "sensei": "code",
    "master ai": "code",
    "browser": "chrome",
    "email": "thunderbird",
    "mail": "thunderbird",
}

PINS_FILE = Path.home() / ".config" / "ai-controller" / "pinned_snippets.json"
LAST_TERM_FILE = Path("/tmp/tahuti-last-term.pid")


def _pin_commands() -> dict:
    """Operator's exec pins: {label: shell text}. Same registry the slide
    keyboard runs — the model may open these coding agents by name."""
    try:
        pins = json.loads(PINS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for pin in pins if isinstance(pins, list) else []:
        try:
            if isinstance(pin, dict) and pin.get("exec") and pin.get("label") and pin.get("text"):
                out[str(pin["label"]).strip().lower()] = str(pin["text"])
        except Exception:
            continue
    return out


def _reachable_pin_names() -> list[str]:
    """Pin labels open_app can actually reach via the pins fallback.

    _open_app_tool checks APP_ALIASES before pins, so a pin label that's
    also an alias target (e.g. "sensei" -> "code") is shadowed and never
    reached regardless of what's in the pin itself. Excluding those here
    keeps the model's own description of what it can open honest -- it
    used to not even know pins existed at all (observed 2026-10-31: the
    open_app tool description claimed "anything else is refused by the
    harness," which was true for the model's own knowledge even though
    the code underneath already handled pin names correctly).
    """
    try:
        return sorted(name for name in _pin_commands() if name not in APP_ALIASES)
    except Exception:
        return []


def _launch_visible(argv: list[str], tag: str) -> dict:
    """Launch a GUI program visibly. Records PID so 'show me your work'
    can raise the window. Fire-and-forget; never blocks.

    Headless (no DISPLAY): a GUI app has nothing to render into, so this
    fails honestly instead of returning ok:True with a pid that opens
    nothing anyone can see.
    """
    if not os.environ.get("DISPLAY"):
        return {"ok": False, "error": f"no display available (headless) -- can't open {tag}"}
    try:
        proc = subprocess.Popen(
            argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        try:
            LAST_TERM_FILE.write_text(str(proc.pid), encoding="utf-8")
        except Exception:
            pass
        return {"ok": True, "opened": tag, "pid": proc.pid}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _raise_last_term() -> bool:
    """Raise the last launched terminal. True if a window was activated."""
    try:
        pid = int(LAST_TERM_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        return False
    try:
        r = subprocess.run(
            ["xdotool", "search", "--pid", str(pid), "windowactivate"],
            capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def _open_app_tool(name: str) -> dict:
    """Launch an allowlisted app or a named exec pin. Visible always."""
    key = (name or "").strip().lower()
    key = APP_ALIASES.get(key, key)
    candidates = APP_COMMANDS.get(key)
    if candidates:
        for cmd in candidates:
            if shutil.which(cmd[0]) is None:
                continue
            return _launch_visible(cmd, key)
        return {"ok": False, "error": f"app not installed here: {key}"}
    # Pins registry: operator's exec pins (hermes, claude, ...) run with the
    # same trust as the slide keyboard, which executes the identical text.
    # Each pin toggles between variants (plain terminal launch, an update
    # script, a URL, ...) via the slide keyboard's variant_index -- the
    # TOP-LEVEL "text" field _pin_commands() reads is whichever variant is
    # currently active. gnome-terminal/xdg-open variants already pop their
    # own visible window, but a bare-script variant (e.g. the "Upd" variant
    # of some pins is a plain shell script with no window of its own) used
    # to run with stdout/stderr -> DEVNULL unconditionally -- completely
    # silent, zero operator-visible feedback that anything happened at all
    # (observed 2026-10-31). Anything that doesn't already self-launch
    # visibly now gets wrapped in a titled terminal, same as scripts run
    # via _pop_script_terminal elsewhere in this file.
    pins = _pin_commands()
    if key in pins:
        if not os.environ.get("DISPLAY"):
            return {"ok": False, "error": f"no display available (headless) -- can't open pin {key}"}
        text = pins[key]
        self_visible = text.strip().startswith(("gnome-terminal", "xdg-open"))
        try:
            if self_visible:
                argv = ["bash", "-c", text]
            else:
                argv = ["gnome-terminal", f"--title={key}", "--", "bash", "-c",
                        text + '; echo; echo "— done —"; exec bash']
            proc = subprocess.Popen(
                argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            try:
                LAST_TERM_FILE.write_text(str(proc.pid), encoding="utf-8")
            except Exception:
                pass
            return {"ok": True, "opened": f"pin:{key}", "pid": proc.pid}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    pin_names = ", ".join(_reachable_pin_names()) or "none configured"
    return {"ok": False, "error": f"app not allowlisted: {name} (chrome, thunderbird, terminal, files, code, or a pin: {pin_names})"}


AOL_MAIL_SCRIPT = os.path.expanduser("~/master-ai-cli/aol_mail.py")
# The real, categorized, multi-account system -- not AOL_MAIL_SCRIPT above,
# which check_email used until 2026-10-31. AOL_MAIL_SCRIPT is only AOL,
# read-only, zero categorization: none of the job/bills/security protection
# work on this system reached Tahuti's own check_email tool at all, even
# though the operator had been using the real system via Claude Code the
# whole time -- confirmed by reading what check_email actually called.
EMAIL_CLEANUP_SCRIPT = os.path.expanduser(
    "~/.hermes/skills/email-inbox-cleanup/scripts/cleanup.py")


def _pop_script_terminal(cmd: list[str], title: str) -> None:
    """Scripts run VISIBLE: pop a terminal so the operator watches it work.

    Best-effort — a headless capture still delivers the summary if no
    display is present. Never raises.
    """
    if not os.environ.get("DISPLAY"):
        return
    try:
        import shlex
        line = " ".join(shlex.quote(c) for c in cmd)
        proc = subprocess.Popen(
            ["gnome-terminal", f"--title={title}", "--", "bash", "-c",
             line + '; echo; echo "— done —"; exec bash'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        # Tracked: "show me your work" raises this terminal.
        try:
            Path("/tmp/tahuti-last-term.pid").write_text(
                str(proc.pid), encoding="utf-8")
        except Exception:
            pass
    except Exception as exc:
        logger.warning("visible terminal failed: %s", exc)


def _check_email_tool(days: int = 2, unread: bool = True) -> dict:
    """Run the real categorized multi-account check (--check, read-only).

    --check never sends, deletes, or moves anything -- that's --run, a
    different flag this tool never passes. Days clamped 1-7, scoped via
    cleanup.py's own --days/--unread (added 2026-10-31 specifically for
    this). /usr/bin/python3 pinned explicitly (not sys.executable --
    ai-controller's own venv happens to also have fastmcp today, but this
    script lives outside that venv and has been run under /usr/bin/python3
    throughout; pinning it avoids depending on that coincidence).
    """
    try:
        days = max(1, min(7, int(days)))
    except Exception:
        days = 2
    if not os.path.isfile(EMAIL_CLEANUP_SCRIPT):
        return {"ok": False, "error": "email reader not installed"}
    cmd = ["/usr/bin/python3", EMAIL_CLEANUP_SCRIPT, "--check", "--days", str(days)]
    if unread:
        cmd.append("--unread")
    # Visible AND captured: terminal pops for watching, capture for speaking.
    _pop_script_terminal(cmd, "Tahuti email check")
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "email check timed out"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    out = (proc.stdout or "")[:4000]
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or out or "reader failed")[:500]}
    return {"ok": True, "output": out}


def _execute_file_tool(goal_id: str, call: dict) -> dict:
    """Execute one tool call and return a JSON-serializable result."""
    name = call.get("name")
    try:
        args = json.loads(call.get("arguments", "{}"))
    except Exception:
        return {"ok": False, "error": "invalid JSON arguments"}
    if name == "read_file":
        return _read_file_tool(
            args.get("path", ""), args.get("offset", 1), args.get("limit")
        )
    if name == "list_dir":
        return _list_dir_tool(args.get("path", ""))
    if name == "write_file":
        return _write_file_tool(
            goal_id,
            args.get("path", ""),
            args.get("content", ""),
            args.get("reason", ""),
        )
    if name == "open_url":
        return _open_url_tool(args.get("url", ""))
    if name == "open_app":
        return _open_app_tool(args.get("name", ""))
    if name == "check_email":
        return _check_email_tool(args.get("days", 2), args.get("unread", True))
    return {"ok": False, "error": f"unknown tool: {name}"}


def _load_conversation_history() -> list[dict]:
    try:
        data = json.loads(CONVERSATION_HISTORY_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _save_conversation_turn(user_text: str, assistant_text: str) -> None:
    """Append this exchange and trim to the last CONVERSATION_HISTORY_MAX_TURNS
    turns. Best-effort -- memory is a convenience, never a reason to fail a
    goal that already has its real answer."""
    if not user_text or not assistant_text:
        return
    try:
        history = _load_conversation_history()
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": assistant_text})
        history = history[-(CONVERSATION_HISTORY_MAX_TURNS * 2):]
        CONVERSATION_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONVERSATION_HISTORY_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(history, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, CONVERSATION_HISTORY_FILE)
    except Exception as exc:
        logger.warning("conversation history save failed: %s", exc)


def _call_model(transcript: str, goal_id: str = "", effort: str = "") -> tuple[str, str, str, float, str]:
    t0 = time.time()
    model, default_effort, max_tokens, route = _model_for_transcript(transcript)
    effort = effort or default_effort
    if not META_KEY:
        raise RuntimeError("MODEL_API_KEY not available")

    # fast paths skip heavy tool lists to cut latency
    if route.startswith("fast-"):
        if route == "fast-lookup":
            tools: list[dict] = [{"type": "web_search"}] if any(
                w in transcript.lower() for w in SIMPLE_LOOKUP_TRIGGERS
            ) or "?" in transcript else []
            # include email tool if transcript asks email (still fast)
            if _EMAIL_TRIGGER_RE.search(transcript):
                tools.append(EMAIL_TOOL)
        else:  # fast-chat
            tools = []
            if _EMAIL_TRIGGER_RE.search(transcript):
                tools = [EMAIL_TOOL]
        # open_url/open_app back in when actually asked -- cheap single-shot
        # calls, not what the fast lanes are too thin for. Without this the
        # model hallucinates having opened something it was never given the
        # tool to open (see OPEN_TOOLS comment above).
        if _OPEN_TRIGGER_RE.search(transcript):
            tools.extend(_tools_with_live_open_app(OPEN_TOOLS))
    else:
        tools = _all_tools(transcript)

    instructions = SAFETY_PROMPT
    try:
        pins = sorted(_pin_commands().keys())
        if pins:
            instructions += (
                " Coding agents you can open by name right now: "
                + ", ".join(pins)
                + ". The native one is 'code' (Master AI Sensei)."
            )
    except Exception:
        pass
    history = _load_conversation_history()
    payload: dict = {
        "model": model,
        "instructions": instructions,
        "input": (history + [{"role": "user", "content": transcript}]
                  if history else transcript),
        "reasoning": {"effort": effort},
        "tools": tools,
    }
    if max_tokens:
        # Responses API uses max_output_tokens
        payload["max_output_tokens"] = max_tokens
    response_id: Optional[str] = None

    try:
        # shorter timeout for fast routes
        timeout = 30.0 if route.startswith("fast-") else MODEL_TIMEOUT
        with httpx.Client(timeout=timeout) as client:
            while True:
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
                response_id = data.get("id") or response_id

                function_calls = [
                    item
                    for item in data.get("output", [])
                    if isinstance(item, dict) and item.get("type") == "function_call"
                ]
                if not function_calls:
                    break

                outputs = []
                for call in function_calls:
                    result = _execute_file_tool(goal_id, call)
                    outputs.append(
                        {
                            "type": "function_call_output",
                            "call_id": call.get("call_id", "unknown"),
                            "output": json.dumps(result),
                        }
                    )

                payload = {
                    "model": model,
                    "previous_response_id": response_id,
                    "input": outputs,
                    "tools": tools,
                }
                if max_tokens:
                    payload["max_output_tokens"] = max_tokens

            text = _extract_responses_text(data)
            if not text:
                text = "I thought about it, but didn't have a reply."
            elapsed_s = time.time() - t0
            elapsed = f"{elapsed_s:.1f}s"
            logger.info("model %s route %s elapsed %.2fs", model, route, elapsed_s)
            return text, model, elapsed, elapsed_s, route
    except Exception as exc:
        logger.warning("Meta worker failed, trying free fallback: %s", exc)
        return _call_free_fallback(transcript, t0, route)


def _call_free_fallback(transcript: str, t0: float, route: str = "fallback") -> tuple[str, str, str, float, str]:
    """Direct-answer fallback on OpenRouter :free models. No tools by design.

    FALLBACK_MODEL is a reasoning model: it spends tokens on an internal
    chain-of-thought (OpenRouter's "reasoning" field) before ever writing
    the user-facing "content" field. At max_tokens=512 that reasoning ate
    the entire budget and finish_reason came back "length" with content
    null -- the thinking never got to finish, not a real empty answer
    (observed 2026-11-01, during a live 400 from the primary Meta worker).
    2048 gives it room for both; verified live: finish_reason "stop" with
    real content at 2048 on the same prompt that starved at 512.
    """
    key = _load_openrouter_key()
    if not key:
        raise RuntimeError("Meta failed and no OPENROUTER_API_KEY available")
    body = json.dumps({
        "model": FALLBACK_MODEL,
        "messages": [
            {"role": "system", "content": SAFETY_PROMPT},
            {"role": "user", "content": transcript},
        ],
        "max_tokens": 2048,
    }).encode()
    req = urllib.request.Request(
        FALLBACK_CHAT_URL, data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
        if data.get("error"):
            raise RuntimeError(f"fallback error: {data['error'].get('message', data['error'])}")
        text = (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
    except Exception as exc:
        logger.warning("Free fallback call failed: %s", exc)
        text = ""
    if not text:
        # Both the primary worker and the fallback came up empty -- say so
        # plainly instead of surfacing a Python exception string as the
        # spoken/notified answer.
        elapsed_s = time.time() - t0
        elapsed = f"{elapsed_s:.1f}s"
        return ("I couldn't get an answer from either model just now -- "
                "try again in a moment."), f"{FALLBACK_MODEL} (empty)", elapsed, elapsed_s, route
    elapsed_s = time.time() - t0
    elapsed = f"{elapsed_s:.1f}s"
    logger.info("Free fallback (%s) answered", FALLBACK_MODEL)
    return text, FALLBACK_MODEL, elapsed, elapsed_s, route


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


_LAST_RESPONSE_LOCK = threading.Lock()


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
        # Concurrent goals (ThreadPoolExecutor, see main()) can finish at
        # nearly the same instant. write_text() truncates-then-writes with
        # no locking of its own -- two unsynchronized writers could
        # interleave into a corrupted file. This is a display-only
        # convenience file, not control flow, but cheap to make safe.
        with _LAST_RESPONSE_LOCK:
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
    elapsed_s: float | None = None,
    route: str = "",
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
        "elapsed_s": elapsed_s,
        "route": route,
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


def _verb_for_approval(transcript: str) -> str:
    """First approval verb in the transcript, for the record's pattern_key."""
    words = set(re.findall(r"[a-z]+", transcript.lower()))
    for verb in sorted(words & APPROVAL_VERBS):
        return verb
    return "action"


def _load_grants() -> dict:
    """Shared grants file (box writes, runner reads): {"patterns": {key: "always"}}.

    Master AI gate semantics: an "always" grant covers background headless runs.
    Missing/corrupt file = no grants (fail closed). Reloaded every goal.
    """
    try:
        data = json.loads(GRANTS_FILE.read_text(encoding="utf-8"))
        patterns = data.get("patterns", {})
        return patterns if isinstance(patterns, dict) else {}
    except Exception:
        return {}


def _is_granted(pattern_key: str) -> bool:
    return _load_grants().get(pattern_key) == "always"


_AVATAR_APPROVAL = Path(os.path.expanduser("~/master-ai-cli/ninja/avatar_approval.py"))


def _render_approval_card(pattern_key: str, description: str, extra: dict) -> str:
    """The run card text: QUESTION first, then Who/What/Where/When/Why/How."""
    try:
        import tahuti_avatar

        card = tahuti_avatar.card_details(pattern_key, description, extra)
    except Exception:
        card = {
            "who": "Tahuti — your AI Controller",
            "what": description,
            "where": "Your active Linux desktop",
            "when": "Immediately after you choose Yes",
            "why": "This action needs your approval before Tahuti runs it",
            "how": "Tahuti goal runner (background)",
        }
    lines = [
        f"QUESTION: {description}",
        "",
        f"Who: {card['who']}",
        f"What: {card['what']}",
        f"Where: {card['where']}",
        f"When: {card['when']}",
        f"Why: {card['why']}",
        f"How: {card['how']}",
        "",
        "Executor: Tahuti goal runner",
        f"Model: {DEFAULT_MODEL}",
    ]
    return "\n".join(lines)


def _present_approval(pattern_key: str, description: str, **extra) -> Optional[str]:
    """Grow the approval card out of the avatar; None when no one can answer.

    The card lives in the avatar's run card (ninja overlay), never in a
    window owned by this daemon: the existing avatar surface is invoked
    as a subprocess and its verdict comes back on stdout. Headless (no
    DISPLAY) there is no one to ask, so this returns None and callers
    keep the fail-closed queue behavior.
    Verdict: "once" | "session" | "deny" ("always" maps to "session":
    the runner's grants file is the persistent form of that grant).
    """
    if not os.environ.get("DISPLAY"):
        return None
    # One avatar on screen during the decision: retire the work pulse.
    try:
        pid = int(Path("/tmp/tahuti-pulse.pid").read_text(encoding="utf-8").strip())
        os.kill(pid, 9)
    except Exception:
        pass
    try:
        proc = subprocess.run(
            ["/usr/bin/python3", str(_AVATAR_APPROVAL), "--ask"],
            input=_render_approval_card(pattern_key, description, extra),
            capture_output=True,
            text=True,
            timeout=150,
            env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
        )
    except Exception as exc:
        logger.warning("approval card unavailable: %s", exc)
        return None
    lines = [ln.strip().lower() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    verdict = lines[-1] if lines else ""
    return {"once": "once", "session": "session",
            "always": "session", "deny": "deny"}.get(verdict)


def _save_grant(pattern_key: str) -> None:
    """Record an 'always' grant for pattern_key (the box's session verdict)."""
    try:
        GRANTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {}
        if GRANTS_FILE.exists():
            try:
                data = json.loads(GRANTS_FILE.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        patterns = data.get("patterns")
        if not isinstance(patterns, dict):
            patterns = {}
        patterns[pattern_key] = "always"
        data["patterns"] = patterns
        tmp = GRANTS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, GRANTS_FILE)
    except Exception as exc:
        logger.warning("grant save failed: %s", exc)


def _needs_approval(transcript: str) -> bool:
    words = set(re.findall(r"[a-z]+", transcript.lower()))
    return bool(words & APPROVAL_VERBS)


def _write_approval_request(goal_id: str, goal: dict) -> None:
    APPROVALS_DIR.mkdir(parents=True, exist_ok=True)
    path = APPROVALS_DIR / f"{goal_id}.pending.json"
    transcript = goal.get("transcript", "")
    record = {
        "id": goal_id,
        "transcript": transcript,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "reason": "irreversible or desktop/shell action requested",
        # Shared vocabulary with master-ai-cli's approval_gate (cua:<action>:<mode>
        # pattern): the approval box grants by pattern_key. Headless runs in
        # background mode — a foreground grant must never cover these.
        "pattern_key": f"tahuti:{_verb_for_approval(transcript)}:background",
    }
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def _show_work_avatar() -> None:
    """Raise the avatar for the duration of a job (2026-10-31): the animation
    shows WHILE working, fed live by the caption file (working/done states).
    Kills any previous pulse so windows never stack. Fire-and-forget.

    Headless (no DISPLAY): nothing to raise -- skip the spawn outright
    instead of letting it fail inside the try/except below.
    """
    pidfile = Path("/tmp/tahuti-pulse.pid")
    try:
        old = int(pidfile.read_text(encoding="utf-8").strip())
        os.kill(old, 9)
    except Exception:
        pass
    if not os.environ.get("DISPLAY"):
        return
    try:
        proc = subprocess.Popen(
            ["/usr/bin/python3",
             os.path.expanduser("~/master-ai-cli/ninja/avatar_approval.py"),
             "--pulse", "180"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
            env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")})
        pidfile.write_text(str(proc.pid), encoding="utf-8")
    except Exception as exc:
        logger.warning("work avatar failed: %s", exc)


def _process_show() -> str:
    """Deterministic 'show me your work': raise the live agent terminal.

    Watching-it-work means seeing the session, not a markdown file. Falls
    back to opening the last response file when no terminal is tracked.

    Headless (no DISPLAY): there's no window or viewer to raise or open --
    read back the last response's own text instead of claiming something
    rendered when nothing could have.
    """
    if os.environ.get("DISPLAY"):
        if _raise_last_term():
            return "Showing my work — that's the live session."
        try:
            if LAST_RESPONSE_FILE.exists():
                subprocess.Popen(
                    ["xdg-open", str(LAST_RESPONSE_FILE)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                return "Showing my work."
        except Exception as exc:
            logger.warning("show failed: %s", exc)
    try:
        if LAST_RESPONSE_FILE.exists():
            text = LAST_RESPONSE_FILE.read_text(encoding="utf-8").strip()
            if text:
                return text[:1000]
    except Exception as exc:
        logger.warning("show (headless) failed to read last response: %s", exc)
    return "No work yet — give me a task first."


def _process_goal(path: Path) -> None:
    claim_path = path.with_suffix(".claim")
    try:
        os.replace(path, claim_path)
    except OSError as exc:
        logger.debug("Could not claim %s: %s", path, exc)
        return
    _show_work_avatar()

    try:
        with open(claim_path, "r", encoding="utf-8") as f:
            goal = json.load(f)
    except Exception as exc:
        logger.error("Invalid goal file %s: %s", claim_path, exc)
        _archive(claim_path, path.stem, "failed", error=f"invalid json: {exc}")
        return

    goal_id = goal.get("id") or path.stem
    missing = [f for f in vgs.GOAL_REQUIRED_FIELDS if f not in goal or goal[f] in (None, "")]
    if missing:
        logger.error("Goal %s missing required fields: %s", goal_id, missing)
        _archive(claim_path, goal_id, "failed", error=f"missing required fields: {', '.join(missing)}")
        return

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
    _write_caption(transcript, "working")

    # Deterministic intents bypass the model entirely: zero tokens, instant.
    if goal.get("type") == "show":
        response = _process_show()
        worker = "tahuti-runner-v1 (show)"
        _post_speak(response)
        try:
            work = LAST_RESPONSE_FILE.read_text(encoding="utf-8")[:1000] \
                if LAST_RESPONSE_FILE.exists() else "nothing yet"
        except Exception:
            work = "nothing yet"
        _notify("Showing work", work, urgency="normal", sound=True)
        _archive(claim_path, goal_id, "done", response=response,
                 model="deterministic-show")
        return

    approval = _needs_approval(transcript)
    response = ""
    worker = ""
    model: Optional[str] = None
    elapsed_s: float | None = None
    route: str = ""

    if approval:
        pattern = f"tahuti:{_verb_for_approval(transcript)}:background"
        if _is_granted(pattern):
            logger.info("grant covers %s — proceeding without the box", pattern)
            approval = False
        else:
            _write_approval_request(goal_id, goal)
            outcome = _present_approval(pattern, transcript)
            if outcome in ("once", "session"):
                if outcome == "session":
                    _save_grant(pattern)
                logger.info("approval box granted %s (%s) — proceeding", pattern, outcome)
                approval = False
            elif outcome == "deny":
                response = "Okay — I won't do that."
                worker = "tahuti-runner-v1 (approval denied)"
            else:
                response = "That request needs your approval before I can act. I've queued it for you."
                worker = "tahuti-runner-v1 (approval gate)"
    if not approval and not response:
        try:
            response, model, elapsed, elapsed_s, route = _call_model(
                transcript, goal_id=goal_id,
                effort=_effort_for_goal(goal, transcript))
            worker = f"tahuti-runner-v1 ({elapsed} {route})"
            _save_conversation_turn(transcript, response)
        except Exception as exc:
            logger.exception("Model call failed for goal %s", goal_id)
            response = f"I ran into a problem: {exc}"
            worker = "tahuti-runner-v1 (model error)"

    _post_speak(response)
    _write_last_response(transcript, worker, response, model=model)
    _write_caption(response if response else transcript, "done")
    # Full answer in the push body (not a snippet): the phone pane shows the
    # work itself — Termux/Termux read the notification, zero extra build.
    _notify(
        "Goal needs approval" if approval else "Goal done",
        response[:1000] if response else transcript[:120],
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
        elapsed_s=elapsed_s,
        route=route,
    )
    # Hold-to-show (2026-10-31): job over = window gone. The spoken answer
    # and response file carry the result; next claim raises the next window.
    try:
        _pp = Path("/tmp/tahuti-pulse.pid").read_text(encoding="utf-8").strip()
        os.kill(int(_pp), 9)
    except Exception:
        pass


def _sleep_or_stop(seconds: float) -> None:
    deadline = time.time() + seconds
    while _running and time.time() < deadline:
        time.sleep(min(0.2, deadline - time.time()))


MAX_CONCURRENT_GOALS = int(os.environ.get("TAHUTI_RUNNER_MAX_CONCURRENT", "3"))


def main() -> int:
    GOALS_DIR.mkdir(parents=True, exist_ok=True)
    DONE_DIR.mkdir(parents=True, exist_ok=True)
    FAILED_DIR.mkdir(parents=True, exist_ok=True)
    APPROVALS_DIR.mkdir(parents=True, exist_ok=True)

    logger.info("Watching %s for goals", GOALS_DIR)

    # Previously a strictly sequential `for path in goals: _process_goal(path)`
    # -- each goal blocked on a full synchronous model round-trip (API call,
    # tool loop, free-fallback retry) before the next one even started. A
    # second Ctrl+F13 press wrote its goal file instantly but the runner
    # wouldn't touch it until the first finished entirely -- observed
    # 2026-10-31 as "can't make multiple calls back to back." A bounded
    # thread pool lets several goals run concurrently instead. This is safe
    # because _process_goal's first step is an atomic os.replace() claim
    # (path -> path.claim) -- two threads racing for the same file can never
    # both win it, so nothing here changes what was already a single-claim
    # guarantee, it just lets DIFFERENT goal files run in parallel.
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_GOALS, thread_name_prefix="tahuti-goal") as pool:
        while _running:
            try:
                goals = sorted(GOALS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
                if goals:
                    list(pool.map(_process_goal, goals))
            except Exception as exc:
                logger.exception("Runner loop error: %s", exc)
        _sleep_or_stop(POLL_INTERVAL)

    logger.info("Tahuti goal runner exiting")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
