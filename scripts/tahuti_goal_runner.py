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

META_RESPONSES_URL = "https://api.meta.ai/v1/responses"
DEFAULT_MODEL = os.environ.get("TAHUTI_RUNNER_MODEL", "muse-wonder-1.0")
CODE_MODEL = "muse-spark-1.1"
BRIDGE_URL = (os.environ.get("BRIDGE_URL", "http://127.0.0.1:8002")).rstrip("/")
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
    {
        "type": "function",
        "name": "check_email",
        "description": (
            "Check the operator's email NOW (live, read-only, no approval needed). "
            "Returns the recent message list (UID, date, from, subject). Use this "
            "whenever email, mail, or inbox is asked about — then summarize it "
            "in the reply. Cannot send, delete, or fetch bodies beyond headers."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Look back N days (1-7, default 2)"},
                "unread": {"type": "boolean", "description": "Unread only (default true)"},
            },
            "additionalProperties": False,
        },
    },
]

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


def _model_for_transcript(text: str) -> tuple[str, str]:
    """Return (model, reasoning_effort)."""
    lowered = text.lower()
    if any(h in lowered for h in CODE_HINTS):
        return CODE_MODEL, "medium"
    return DEFAULT_MODEL, "minimal"


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


def _all_tools(transcript: str) -> list[dict]:
    """Tools offered for this transcript: file tools plus optional web search."""
    tools: list[dict] = list(FILE_TOOLS)
    lowered = transcript.lower()
    if "?" in transcript or any(
        w in lowered
        for w in {"weather", "news", "lookup", "find", "search", "what", "who", "when", "where", "why", "how"}
    ):
        tools.append({"type": "web_search"})
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
        # The singleton native worker owns presentation and execution. Wait for
        # its verified result so the model cannot mistake queued for completed.
        subprocess.Popen(
            ['/usr/bin/python3', str(Path(__file__).with_name('tahuti_approval_ui.py'))],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        result_path = approval_path.with_name(approval_path.name.replace('.pending.json', '.resolved.json'))
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


def _launch_visible(argv: list[str], tag: str) -> dict:
    """Launch a GUI program visibly. Records PID so 'show me your work'
    can raise the window. Fire-and-forget; never blocks."""
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
    pins = _pin_commands()
    if key in pins:
        try:
            proc = subprocess.Popen(
                ["bash", "-c", pins[key]],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            try:
                LAST_TERM_FILE.write_text(str(proc.pid), encoding="utf-8")
            except Exception:
                pass
            return {"ok": True, "opened": f"pin:{key}", "pid": proc.pid}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": False, "error": f"app not allowlisted: {name} (chrome, thunderbird, terminal, files, code, or a pin name)"}


AOL_MAIL_SCRIPT = os.path.expanduser("~/master-ai-cli/aol_mail.py")


def _pop_script_terminal(cmd: list[str], title: str) -> None:
    """Scripts run VISIBLE: pop a terminal so the operator watches it work.

    Best-effort — a headless capture still delivers the summary if no
    display is present. Never raises.
    """
    try:
        import shlex
        line = " ".join(shlex.quote(c) for c in cmd)
        subprocess.Popen(
            ["gnome-terminal", f"--title={title}", "--", "bash", "-c",
             line + '; echo; echo "— done —"; exec bash'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
    except Exception as exc:
        logger.warning("visible terminal failed: %s", exc)


def _check_email_tool(days: int = 2, unread: bool = True) -> dict:
    """Run the vendored read-only AOL IMAP reader. No shell, fixed argv.

    The script only SELECTs readonly / PEEKs headers — it cannot send,
    delete, or mark anything. Days clamped 1-7; folder fixed to INBOX;
    no search string (IMAP injection surface stays closed).
    """
    try:
        days = max(1, min(7, int(days)))
    except Exception:
        days = 2
    if not os.path.isfile(AOL_MAIL_SCRIPT):
        return {"ok": False, "error": "email reader not installed"}
    cmd = [sys.executable, AOL_MAIL_SCRIPT, "--days", str(days)]
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


def _call_model(transcript: str, goal_id: str = "", effort: str = "") -> tuple[str, str, str]:
    t0 = time.time()
    model, default_effort = _model_for_transcript(transcript)
    effort = effort or default_effort
    if not META_KEY:
        raise RuntimeError("MODEL_API_KEY not available")

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
    payload: dict = {
        "model": model,
        "instructions": instructions,
        "input": transcript,
        "reasoning": {"effort": effort},
        "tools": tools,
    }
    response_id: Optional[str] = None

    try:
        with httpx.Client(timeout=MODEL_TIMEOUT) as client:
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

            text = _extract_responses_text(data)
            if not text:
                text = "I thought about it, but didn't have a reply."
            elapsed = f"{time.time() - t0:.1f}s"
            return text, model, elapsed
    except Exception as exc:
        logger.warning("Meta worker failed, trying free fallback: %s", exc)
        return _call_free_fallback(transcript, t0)


def _call_free_fallback(transcript: str, t0: float) -> tuple[str, str, str]:
    """Direct-answer fallback on OpenRouter :free models. No tools by design."""
    key = _load_openrouter_key()
    if not key:
        raise RuntimeError("Meta failed and no OPENROUTER_API_KEY available")
    body = json.dumps({
        "model": FALLBACK_MODEL,
        "messages": [
            {"role": "system", "content": SAFETY_PROMPT},
            {"role": "user", "content": transcript},
        ],
        "max_tokens": 512,
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
        raise RuntimeError(f"Free fallback failed: {exc}") from exc
    if not text:
        raise RuntimeError("Free fallback returned empty text")
    elapsed = f"{time.time() - t0:.1f}s"
    logger.info("Free fallback (%s) answered", FALLBACK_MODEL)
    return text, FALLBACK_MODEL, elapsed


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


def _process_show() -> str:
    """Deterministic 'show me your work': raise the live agent terminal.

    Watching-it-work means seeing the session, not a markdown file. Falls
    back to opening the last response file when no terminal is tracked.
    """
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
        return "No work yet — give me a task first."
    except Exception as exc:
        logger.warning("show failed: %s", exc)
        return "I couldn't open it, but the last answer is saved."


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

    if approval:
        pattern = f"tahuti:{_verb_for_approval(transcript)}:background"
        if _is_granted(pattern):
            logger.info("grant covers %s — proceeding without queue", pattern)
            approval = False
        else:
            _write_approval_request(goal_id, goal)
            response = "That request needs your approval before I can act. I've queued it for you."
            worker = "tahuti-runner-v1 (approval gate)"
    else:
        try:
            response, model, elapsed = _call_model(
                transcript, goal_id=goal_id,
                effort=_effort_for_goal(goal, transcript))
            worker = f"tahuti-runner-v1 ({elapsed})"
        except Exception as exc:
            logger.exception("Model call failed for goal %s", goal_id)
            response = f"I ran into a problem: {exc}"
            worker = "tahuti-runner-v1 (model error)"

    _post_speak(response)
    _write_last_response(transcript, worker, response, model=model)
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
