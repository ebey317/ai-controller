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
import shlex
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
# code-fast was hardcoded at 1024 inline in _model_for_transcript (not an
# env-overridable constant like the other two fast lanes) and had NEVER
# been bumped -- audited 2026-11-01 after fixing the same bug on
# fast-chat/fast-lookup, found this lane dead-ending on EVERY real tool
# round: "read the file test.py for me" and "fix the bug in my script
# please" both hit "I thought about it, but didn't have a reply." 4/4
# live runs. Worse than the other two lanes because code-fast gets the
# FULL tool set (_all_tools, not a reduced list) plus effort="low" (more
# reasoning than "minimal") -- more budget pressure, same 1024 cap.
CODE_FAST_MAX_TOKENS = int(os.environ.get("TAHUTI_CODE_FAST_MAX_TOKENS", "2048"))
FAST_CHAT_MAX_TOKENS = int(os.environ.get("TAHUTI_FAST_CHAT_MAX_TOKENS", "1536"))
# Same reasoning-starves-the-budget failure as FAST_LOOKUP_MAX_TOKENS below,
# found live 2026-11-01 while verifying the tool-allowlist/honesty-prompt
# fix: at 256, "reasoning" alone (even effort="minimal") used the whole
# budget and the response came back with ZERO message text ("status":
# "incomplete" on the reasoning item) -- the model never got to write its
# honest "I have no tools this turn" answer, it just came back empty and
# fell through to "I thought about it, but didn't have a reply." 1024
# fixed the no-tools case but a real tool round (reasoning + function_call
# + a second reasoning pass + the final message, all sharing one budget)
# still starved intermittently -- observed live, ~1/3 of real open_url
# calls, same "incomplete" reasoning / zero-text shape. Bumped to 1536 and
# re-verified: 5/5 live open_url calls completed with real answer text.
# 512 starved on anything needing a second tool round (open_url then a
# web_search to verify/summarize): the model spent the whole budget on
# reasoning + the search calls and never reached the final text, coming
# back as "I thought about it, but didn't have a reply." -- observed and
# fixed 2026-11-01 alongside the open_url hallucination bug above. 1536
# verified live, same round-trip pattern as FAST_CHAT_MAX_TOKENS above.
FAST_LOOKUP_MAX_TOKENS = int(os.environ.get("TAHUTI_FAST_LOOKUP_MAX_TOKENS", "1536"))
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
# Caps tool-call rounds in _call_model's loop -- no max_output_tokens number
# fixes an open-ended search: "read the file test.py" with no exact match
# had the model trying more and more candidate directories round after
# round regardless of budget (observed live 2026-11-01, code-fast route,
# 6+ rounds, 90-96s, still spiraling even after the home-directory-guessing
# bug above was fixed). After MAX_TOOL_ROUNDS rounds, the next request's
# "tools" is sent as [] -- the model can no longer call anything and MUST
# write a real text answer from what it already found. See the hard
# ceiling in _call_model for the (rare) case it still emits a function_call
# despite that.
MAX_TOOL_ROUNDS = int(os.environ.get("TAHUTI_MAX_TOOL_ROUNDS", "4"))

SAFETY_PROMPT_HEAD = (
    "You are Tahuti, the headless agent on the operator's Linux desktop — "
    "a model with live tools, and you know your framework. "
)

# Per-tool one-liners for the dynamic "YOUR TOOLS this turn" sentence built
# in _tools_sentence() below. Keep these in sync with FILE_TOOLS/EMAIL_TOOL
# descriptions -- this dict exists only to render a short prompt clause.
TOOL_DESCRIPTIONS = {
    "read_file": "read_file (read a text file, home directory only)",
    "list_dir": "list_dir (list a directory, home directory only)",
    "write_file": "write_file (queues an approval request in ~/.tahuti/approvals, never writes directly)",
    "open_url": "open_url (open any website now)",
    "open_app": "open_app (launch an app now)",
    "check_email": "check_email (read-only inbox check)",
    "check_commits": "check_commits (read-only recent git commits across your repos)",
    "request_approval": "request_approval (raise the operator's REAL approval card for a gated action)",
}

SAFETY_PROMPT_TAIL = (
    "Decide per task which tool fits and act immediately on safe, reversible "
    "ones — no approval needed. YOUR HARNESS: controller L+R and the keyboard "
    "AGENT toggle file goals to ~/.tahuti/goals; your final message is spoken "
    "aloud and saved. "
    "APPROVAL IS A TOOL CALL, NOT A FACT ABOUT THE WORLD: the ONLY way "
    "anything lands in ~/.tahuti/approvals is you calling write_file THIS "
    "turn and getting back {\"ok\": true} — nothing queues itself "
    "automatically just because a task needs approval. If write_file is not "
    "in your tools this turn, or you didn't call it, you have NOT filed "
    "anything — say the task needs the operator's approval and stop there; "
    "never say 'I've added/queued/noted/filed this for approval' unless "
    "write_file's result this turn literally says ok. "
    "CLAF (tiered model router) and the Hermes gateway also "
    "live on this machine, but you cannot call them — never claim otherwise. "
    "The native coding agent is Master AI Sensei — always name it exactly "
    "that, never Claude or another agent. "
    "HARD NOS: no shell or terminal, no clicking or typing, no services, "
    "no installs, no deletes, no sending or buying — you have no tools "
    "that do those. If the operator's task needs one, call request_approval "
    "NOW with the action and a one-line reason: the operator's approval "
    "card appears on their screen and the tool result tells you their real "
    "decision. Never claim the operator approved anything unless "
    "request_approval's result THIS turn says so; if it comes back denied "
    "or unavailable, say that plainly. If request_approval is not in your "
    "tools this turn, say the task needs the operator's approval and stop "
    "there — never fabricate a decision. "
    "HONESTY RULE: never describe ANY tool action as done — opened, read, "
    "checked, queued, filed, added, noted — unless a real tool result from "
    "THIS turn actually says it succeeded. A tool call that errors, comes "
    "back refused ('not available for this request'), or that you never "
    "actually made is NOT a success — if that happens, say so plainly "
    "('that didn't work' / 'I don't have a way to do that right now') "
    "instead of inventing a plausible-sounding outcome, a fake approval "
    "entry, or an app you didn't really open. SCOPE: report ONLY the exact "
    "tool calls you actually made this turn, for ONLY what THIS message "
    "asked — never pad your answer with extra apps/files/approvals you "
    "didn't call just because earlier conversation turns mentioned them. "
    "Never invent a PID, a path, a filename, or any other specific detail "
    "that didn't come back in a real tool result this turn. "
    "Replies concise, plain spoken words, under 3 sentences."
)

# Static fallback text for _call_free_fallback, which has no live tools list
# to describe (it is a last-resort, tool-free path) -- kept separate from the
# dynamic per-request prompt built by _tools_sentence() in _call_model.
SAFETY_PROMPT = (
    SAFETY_PROMPT_HEAD
    + "YOU HAVE NO TOOLS on this path — this is a text-only fallback. "
      "Answer from what you already know; never claim to open, read, check, "
      "or send anything. "
    + SAFETY_PROMPT_TAIL
)


def _tools_sentence(tools: list[dict]) -> str:
    """Build the "YOUR TOOLS this turn" prompt clause from the tools actually
    declared in THIS request's "tools" array, instead of the old hardcoded
    sentence that told the model it always had write_file/open_app/etc.
    regardless of what this round's tools list actually contained.

    That mismatch was the root cause of the fabricated "queued for
    approval"/"opened Master AI Sensei" claims observed 2026-11-01: fast-chat
    sends tools=[], the model hallucinated a tool call anyway, the executor
    correctly refused it post-allowlist-fix, but the prompt still told the
    model unconditionally that write_file/open_app existed -- so its recovery
    text fabricated a plausible success instead of admitting neither tool
    was ever on the table this turn.
    """
    names = [t.get("name") for t in tools if t.get("name")]
    has_web_search = any(t.get("type") == "web_search" for t in tools)
    parts = [TOOL_DESCRIPTIONS[n] for n in names if n in TOOL_DESCRIPTIONS]
    if has_web_search:
        parts.append("web_search (offered on lookup tasks)")
    if not parts:
        return (
            "YOUR TOOLS this turn: none. You can only answer from what you "
            "already know for this request — say so plainly if that's not "
            "enough. Don't pretend to check, open, read, or queue anything."
        )
    sentence = (
        "YOUR TOOLS this turn: " + ", ".join(parts) + ". "
        "Nothing else is real for this request, no matter what it looked "
        "like you could do a moment ago."
    )
    # Tell the model the real home path instead of making it guess one --
    # the bare-filename auto-resolve fix in _read_file_tool/_list_dir_tool
    # (2026-11-01) covers a plain filename, but the model still needs the
    # literal path for anything it wants to state as absolute, or to
    # reason about a specific subdirectory by name. Without this it
    # reliably guessed wrong generic Linux homes (/home/ubuntu, /root,
    # /tmp, ...) for a machine it's never actually seen.
    if any(n in ("read_file", "list_dir", "write_file") for n in names):
        sentence += (
            f" The operator's home directory is literally {Path.home()} — "
            "that's the real path on THIS machine, not a guess. A bare "
            "filename or relative path (e.g. \"test.py\", \"Downloads\") "
            "resolves there automatically, so you never need to spell out "
            "the full path yourself. Never guess a different system's home "
            "(/home/ubuntu, /home/user, /root, /tmp, ...) — that's not this "
            "machine and will always be refused."
        )
    return sentence

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
            "Read a text file and return its contents. "
            "Use this to inspect code, logs, notes, or documents. "
            "Only the user's home directory is allowed -- give an absolute "
            "path if you know one, or just a bare filename/relative path "
            "(e.g. \"test.py\" or \"notes/todo.txt\") and it resolves under "
            "the operator's home directory automatically. Don't guess a "
            "generic absolute path like /home/ubuntu or /root -- you don't "
            "know this machine's layout, a bare name is always safer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path, or a bare filename/relative path to resolve under home"},
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
            "List the entries in a directory. Use this to explore the "
            "workspace. Only the user's home directory is allowed -- give "
            "an absolute path if you know one, or a bare name/relative path "
            "(e.g. \"Downloads\", or \"\" for the home directory itself) and "
            "it resolves under the operator's home automatically. Don't "
            "guess a generic absolute path like /home/ubuntu or /root. "
            "Dotfiles/dot-directories (app config noise) are hidden by "
            "default and results are capped -- if you're hunting for a "
            "specific project, drill into a named subdirectory (e.g. "
            "\"ai-controller\") rather than re-listing the home root."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path, or a bare/relative path to resolve under home (empty string = home dir itself)"},
                "show_hidden": {"type": "boolean", "description": "Include dotfiles/dot-directories (default false)"},
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

# Added 2026-11-01: "tell me my recent commits" had NO tool to answer with
# at all -- the model honestly said it needed approval, but there was (and
# still is, for everything except file writes) no actual way to file an
# approval request, so nothing ever reached ~/.tahuti/approvals and no box
# ever appeared. Checking commit history is read-only and safe (same
# category as check_email), so the real fix is giving it a tool rather
# than building out generic approval-filing for something that doesn't
# need approval in the first place.
COMMIT_TOOL = {
    "type": "function",
    "name": "check_commits",
    "description": (
        "Check recent git commits across the operator's repositories NOW "
        "(live, read-only, no approval needed). Scans repos under the "
        "home directory (or a specific one if named) and returns recent "
        "commit summaries (hash, date, message). Never commits, pushes, "
        "or modifies anything -- read-only `git log` only. Only call this "
        "when the operator has clearly asked about commits, repos, or "
        "recent code changes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "A specific repo name/relative path to check (optional -- default scans repos found under home)"},
            "days": {"type": "integer", "description": "How many days back to look (1-90, default 14)"},
            "limit": {"type": "integer", "description": "Max commits per repo (1-50, default 10)"},
        },
        "additionalProperties": False,
    },
}

COMMIT_TRIGGER_WORDS = {"commit", "commits", "repo", "repos", "repository", "repositories"}

# Added 2026-11-01: the model previously had NO way to raise the operator's
# approval card mid-goal -- the goal-level keyword gate fires before the
# model is ever called, and write_file is the only tool that shows the box.
# Anything gated but phrased without an APPROVAL_VERB dead-ended in a text
# refusal ("needs approval and I don't have a way to file it"), so the box
# never appeared and the operator never got asked. This tool IS the missing
# seam: the model calls it, the real card appears, the real verdict comes
# back. Fail-closed on every path -- deny, timeout, and headless all return
# {"ok": False} so the model can only ever report the operator's decision.
REQUEST_APPROVAL_TOOL = {
    "type": "function",
    "name": "request_approval",
    "description": (
        "Raise the operator's REAL approval card NOW for one action you "
        "cannot take without permission (shell/terminal, installs, deletes, "
        "sends, services, or anything irreversible). The card appears on "
        "the operator's screen with what you asked and why; the result "
        "tells you their actual decision. Approving authorizes you to "
        "proceed ONLY with the tools you actually have this turn -- if "
        "none of your tools can perform the action, say that plainly "
        "after the decision instead of pretending it ran. Never claim the "
        "operator approved anything unless this tool's result says so."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": (
                    "Short verb phrase naming the gated action, e.g. "
                    "'restart the voice service', 'delete notes.txt'"
                ),
            },
            "reason": {
                "type": "string",
                "description": "One plain sentence: why this needs the operator's go-ahead",
            },
        },
        "required": ["action", "reason"],
        "additionalProperties": False,
    },
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


_CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")


def _split_camel_case(text: str) -> str:
    """Insert a space at lower->upper letter transitions.

    STT sometimes glues a trigger word directly onto the next word with
    no space -- observed live 2026-11-01: a spoken "open facebook.com"
    transcribed as "OpenFacebook.com." (one unbroken run of letters).
    Every word-boundary-anchored regex and tokenized hint set in this
    file (_OPEN_TRIGGER_RE, _EMAIL_TRIGGER_RE, _COMMIT_TRIGGER_RE,
    CODE_HINTS, SIMPLE_LOOKUP_TRIGGERS, MULTISTEP_HINTS, CHAT_SHORT_WORDS,
    APPROVAL_VERBS) requires a real word boundary to fire, so "open" never
    matched inside "OpenFacebook" -- zero tools got offered, and the model
    was left honestly saying "needs approval" with nothing to actually
    call. Call this on the transcript BEFORE any of that matching, not on
    what gets sent to the model or saved to history/thread -- it's a
    matching-only normalization, not a rewrite of what was actually said.
    """
    return _CAMEL_SPLIT_RE.sub(" ", text)


def _is_simple_route(text: str) -> str | None:
    """Classify simple goals for fast path: 'chat', 'lookup', or None."""
    t = (text or "").strip()
    if not t:
        return None
    # Length/word-count checks use the real transcript; matching below
    # uses the camelCase-split version (see _split_camel_case).
    lowered = _split_camel_case(t).lower()
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
    lowered = _split_camel_case(text).lower()
    word_set = set(re.findall(r"[a-z']+", lowered))
    if _contains_hint(lowered, word_set, CODE_HINTS):
        # code still fast but needs a bit more tokens
        return CODE_MODEL, "low", CODE_FAST_MAX_TOKENS, "code-fast"
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


# Secret-path fence (2026-11-01): _read_file_tool had no equivalent of
# Master AI's own _read_path_ok secret-path blocking -- "under home" was
# the only check, and ~/.master_ai_keys, ~/.ssh, etc. are all under home.
# A transcript asking to "read Master AI Keychain" sailed straight through
# with zero gating: APPROVAL_VERBS is action-verb based ("run"/"delete"/
# "send"), never matches a bare read, and this tool itself refused nothing.
# Resolved once at import time (not per-call) -- these paths don't move.
_SECRET_PATHS = [
    p.resolve() for p in (
        Path.home() / ".ssh",
        Path.home() / ".aws",
        Path.home() / ".master_ai_keys",
        Path.home() / "Desktop" / "Projects" / "keychain",
        Path.home() / ".config" / "email_mcp" / "credentials.json",
        Path.home() / ".hermes" / ".env",
        Path.home() / ".config" / "ai-controller" / "config.env",
    )
]


def _is_secret_path(path: Path) -> bool:
    """True if path resolves to, or inside, a known secret location.
    Checks the RESOLVED path so a symlink into a secret directory can't
    bypass this by pointing somewhere that looks innocuous."""
    try:
        resolved = path.resolve()
    except (OSError, ValueError):
        return False
    for secret in _SECRET_PATHS:
        try:
            resolved.relative_to(secret)
            return True
        except ValueError:
            continue
    return False


_EMAIL_TRIGGER_RE = re.compile(
    r"\b(" + "|".join(EMAIL_TRIGGER_WORDS) + r")\b", re.IGNORECASE
)

_COMMIT_TRIGGER_RE = re.compile(
    r"\b(" + "|".join(COMMIT_TRIGGER_WORDS) + r")\b", re.IGNORECASE
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
    lowered = _split_camel_case(transcript).lower()
    if "?" in transcript or any(
        w in lowered
        for w in {"weather", "news", "lookup", "find", "search", "what", "who", "when", "where", "why", "how"}
    ):
        tools.append({"type": "web_search"})
    if _EMAIL_TRIGGER_RE.search(lowered):
        tools.append(EMAIL_TOOL)
    if _COMMIT_TRIGGER_RE.search(lowered):
        tools.append(COMMIT_TOOL)
    # Always offered: the model's only way to raise the operator's card
    # for anything gated, regardless of how the request was phrased.
    tools.append(REQUEST_APPROVAL_TOOL)
    return tools


def _read_file_tool(path: str, offset: int = 1, limit: Optional[int] = None) -> dict:
    p = Path(path)
    if not p.is_absolute():
        # Bare filename ("test.py") resolves under the operator's home
        # instead of refusing -- refusing used to force the model to guess
        # an absolute path for a system it's never seen, and it reliably
        # guessed wrong ones (/home/ubuntu, /root, /tmp, ...), burning a
        # full multi-round tool-call spiral before ever answering (observed
        # live 2026-11-01, 5-6 rounds, eventually escalating to opening an
        # app as a last-ditch "find it another way"). _is_under_home below
        # still gates the resolved path, so "../../etc/passwd" still refuses.
        p = Path.home() / p
    if not _is_under_home(p):
        return {"ok": False, "error": "path outside home directory refused"}
    if _is_secret_path(p):
        return {"ok": False, "error": "refused: this path holds credentials, never readable by this tool"}
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


LIST_DIR_MAX_ENTRIES = 150
# A bare list_dir("") on this operator's actual home returned ALL 378
# entries unfiltered (observed live 2026-11-01, during a "find and review
# the voice bridge code" test) -- 241 of those are dotfiles/dot-dirs
# (app config noise: .claude*, .master_ai_*, .cache, ...), leaving only 137
# real project entries the model actually cares about when hunting for
# code. That one call alone ate a large slice of the whole MAX_TOOL_ROUNDS
# budget, and the model still had to guess blindly through several more
# rounds before finding the right folder -- it ran out of rounds before
# ever reading the file. Hiding dotfiles by default (same as plain `ls`,
# matching normal filesystem convention) and sorting directories first
# (what the model actually drills into) turns one noisy dump into a short,
# navigable list. 150 comfortably covers this exact home (137 real
# entries) while still capping any single directory that could have far
# more (a project's own node_modules, say).


def _list_dir_tool(path: str, show_hidden: bool = False) -> dict:
    p = Path(path)
    if not p.is_absolute():
        # Same bare-name resolution as _read_file_tool above -- "" or "."
        # also lands here and correctly resolves to the home dir itself.
        p = Path.home() / p
    if not _is_under_home(p):
        return {"ok": False, "error": "path outside home directory refused"}
    if _is_secret_path(p):
        return {"ok": False, "error": "refused: this path holds credentials, never listable by this tool"}
    try:
        resolved = p.resolve()
        if not resolved.is_dir():
            return {"ok": False, "error": f"not a directory: {path}"}
        raw = list(resolved.iterdir())
        if not show_hidden:
            raw = [e for e in raw if not e.name.startswith(".")]
        # Dirs first, then alphabetical within each group -- dirs are what
        # the model actually needs to drill into next.
        raw.sort(key=lambda e: (not e.is_dir(), e.name.lower()))
        total = len(raw)
        capped = raw[:LIST_DIR_MAX_ENTRIES]
        entries = []
        for entry in capped:
            try:
                kind = "dir" if entry.is_dir() else "file"
                size = entry.stat().st_size if entry.is_file() else 0
                entries.append({"name": entry.name, "type": kind, "size": size})
            except OSError:
                entries.append({"name": entry.name, "type": "unknown", "size": 0})
        result = {"ok": True, "path": str(resolved), "entries": entries}
        if total > len(capped) or not show_hidden:
            notes = []
            if not show_hidden:
                notes.append("dotfiles/dot-dirs hidden -- pass show_hidden=true to include them")
            if total > len(capped):
                notes.append(f"showing {len(capped)} of {total} non-hidden entries; "
                              "call list_dir on a specific subdirectory name to go deeper")
            result["note"] = "; ".join(notes)
            result["shown"] = len(capped)
            result["total"] = total
        return result
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


def _pop_text_terminal(title: str, text: str) -> None:
    """Fallback for _write_to_agent_thread when Sensei's tmux session isn't
    up: a disposable standalone terminal showing pre-computed text, instead
    of _pop_script_terminal's "run this live command and watch it" shape
    (there's no live command here, just a result to display). Same
    best-effort/never-raise contract, same "no DISPLAY -> do nothing"
    behavior."""
    if not os.environ.get("DISPLAY"):
        return
    try:
        _AGENT_THREAD_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _AGENT_THREAD_DIR / f"{title}-{int(time.time() * 1000)}.txt"
        tmp.write_text(text, encoding="utf-8")
        subprocess.Popen(
            ["gnome-terminal", f"--title=Tahuti: {title}", "--", "bash", "-c",
             f"cat {shlex.quote(str(tmp))}; echo; echo \"— done —\"; exec bash"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
    except Exception as exc:
        logger.warning("text terminal failed: %s", exc)


# ── Agent-framework thread bridge ────────────────────────────────────────
# Operator's ask (2026-11-01): "if I say check my emails, I'd want to see
# the result of that in my agent framework thread -- so I could read it or
# edit it or whatever I wanted to do." Before this, every tool's result
# only reached the operator as a spoken TTS summary plus a line in
# ~/.tahuti/conversation_history.json -- nothing landed anywhere scrollable,
# copyable, or editable. check_email came closest (_pop_script_terminal
# above) but that pops a brand-new, disposable gnome-terminal window with
# no connection to Sensei's own tmux session -- not "the agent framework
# thread" the operator means, and it silently does nothing at all with no
# DISPLAY set (headless boxes).
#
# Deliberately NOT done via tmux send-keys into Sensei's own pane: that
# pane is a live LLM loop reading its own stdin as real user turns (the
# existing agent-dictation mode in slide_keyboard.py's _send_to_agent()
# does exactly that, on purpose, for typed input). Feeding a tool's raw
# result there -- an email body, a file's contents -- would make Sensei's
# own model "receive" that content as if the operator said it and try to
# respond to it: a real indirect-prompt-injection surface (email content
# could contain instructions) for zero benefit, since nothing here needs
# a model turn at all.
#
# Instead: a dedicated plain-bash WINDOW inside the SAME tmux session
# Sensei lives in (same session = genuinely "the agent framework thread" in
# the tmux sense this codebase already uses throughout -- same switchable
# window list, same lifetime, no DISPLAY/X11 needed at all). Content is
# written to a file first and only ever reached by that window's own `cat`
# -- never interpolated into a command string, never seen by any model.
_AGENT_THREAD_WINDOW = "tahuti"
_AGENT_THREAD_DIR = Path.home() / ".tahuti" / "thread_output"


def _agent_session_name() -> str:
    """Same lookup order as slide_keyboard.py's _agent_session_name() and
    master_ai_autostart.sh's $SESSION, so this always targets whichever
    tmux session Sensei actually started in. Duplicated rather than
    imported -- these are independent standalone scripts, not a shared
    module, matching this file's existing convention."""
    name = os.environ.get("SENSEI_SESSION_NAME", "").strip()
    if name:
        return name
    try:
        with open(os.path.expanduser("~/.master_ai_session_name"), "r", encoding="utf-8") as f:
            name = f.read().strip()
            if name:
                return name
    except Exception:
        pass
    return "master-ai"


def _tmux_session_exists(session: str) -> bool:
    try:
        return subprocess.run(
            ["tmux", "has-session", "-t", session],
            capture_output=True, timeout=5,
        ).returncode == 0
    except Exception:
        return False


def _tmux_window_exists(session: str, window: str) -> bool:
    try:
        proc = subprocess.run(
            ["tmux", "list-windows", "-t", session, "-F", "#{window_name}"],
            capture_output=True, text=True, timeout=5,
        )
        return window in (proc.stdout or "").splitlines()
    except Exception:
        return False


def _format_tool_result_for_thread(name: str, args: dict, result: dict) -> str:
    """Readable block for one tool call -- prefers a result's own human
    text (check_email's "output", read_file's "content") over a raw JSON
    dump, falls back to pretty JSON for everything else (list_dir entries,
    open_url/open_app confirmations, errors)."""
    try:
        header = f"{name}({json.dumps(args, ensure_ascii=False)})"
    except Exception:
        header = name
    if not result.get("ok", False):
        return f"{header}\n  REFUSED/ERROR: {result.get('error', 'unknown error')}"
    for key in ("output", "content"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return f"{header}\n{value}"
    return f"{header}\n{json.dumps(result, indent=2, ensure_ascii=False)}"


def _write_to_agent_thread(name: str, args: dict, result: dict) -> bool:
    """Land one tool call's real result in Sensei's tmux session (see
    module-level comment above). Best-effort, matches _pop_script_terminal's
    contract: never raises. Returns False (not an exception) whenever
    Sensei's tmux session isn't up, so the caller can keep its existing
    fallback behavior (e.g. check_email's own _pop_script_terminal popup)
    rather than silently doing nothing."""
    session = _agent_session_name()
    if not _tmux_session_exists(session):
        return False
    try:
        _AGENT_THREAD_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        body = _format_tool_result_for_thread(name, args, result)
        block = f"\n===== TAHUTI: {name}  ({stamp}) =====\n{body.rstrip()}\n"
        tmp = _AGENT_THREAD_DIR / f"{name}-{int(time.time() * 1000)}.txt"
        tmp.write_text(block, encoding="utf-8")

        target = f"{session}:{_AGENT_THREAD_WINDOW}"
        if not _tmux_window_exists(session, _AGENT_THREAD_WINDOW):
            subprocess.run(
                ["tmux", "new-window", "-t", session, "-n", _AGENT_THREAD_WINDOW, "bash"],
                check=False, timeout=5,
            )
            time.sleep(0.3)  # let the shell actually start before typing into it
        # -l sends these exact characters as literal typed input, not a
        # tmux key name -- the path is the only variable part, and it's
        # shell-quoted; the tool's actual content never touches this
        # command line, it only ever reaches `cat`'s own stdout.
        subprocess.run(
            ["tmux", "send-keys", "-t", target, "-l", "--", f"cat {shlex.quote(str(tmp))}"],
            check=False, timeout=5,
        )
        subprocess.run(["tmux", "send-keys", "-t", target, "Enter"], check=False, timeout=5)
        return True
    except Exception as exc:
        logger.warning("agent-thread write failed: %s", exc)
        return False


def _write_answer_to_thread(transcript: str, route: str, text: str) -> None:
    """Land the model's FINAL synthesized answer in the thread, not just
    each tool's raw result. Without this, a task that reads a file and
    writes a review (e.g. "review voice_bridge.py") only ever showed the
    raw file content in the thread -- the actual review text, the thing
    worth reading, still only reached the operator by voice/history
    (observed live 2026-11-01). Reuses _write_to_agent_thread's existing
    choke point/fallback rather than a separate code path. Best-effort,
    never raises -- called from three return points (the main loop and
    both free-fallback branches), so centralizing the try/except here
    keeps those call sites one line each."""
    try:
        landed = _write_to_agent_thread(
            "tahuti_answer",
            {"you_said": transcript[:200], "route": route},
            {"ok": True, "output": text},
        )
        if not landed:
            _pop_text_terminal("answer", f"You said: {transcript[:200]}\n\n{text}")
    except Exception as exc:
        logger.warning("agent-thread answer write failed: %s", exc)


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
    # Was: _pop_script_terminal(cmd, ...) here ran this SAME script a
    # second time just to have something visible, then subprocess.run
    # below ran it again to capture text for speaking -- two real IMAP
    # round-trips for one request. The generic choke point in
    # _execute_file_tool now lands this result in Sensei's own tmux
    # thread (or a fallback popup) AFTER this single run completes, so
    # the second run is no longer needed.
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


MAX_COMMIT_REPOS_SCANNED = 60
# Discovery (os.walk + a stat per candidate dir) is cheap -- measured
# ~0.01s for this operator's whole home -- so this cap exists only as a
# sane outer bound, not a performance lever. Raised 25 -> 60 live
# 2026-11-01: with unsorted os.walk order and a cap of 25, the walk hit
# the cap on template/pack repos (ad-video-skills, manim-skills, ...)
# and refactor-staging mirrors before ever reaching ai-controller itself
# -- the single most relevant repo on this machine was silently missing
# from its own results. The actually expensive part is running `git log`
# once per found repo, which is capped separately by running them in
# parallel below rather than by scanning fewer directories.
MAX_COMMIT_WALK_DEPTH = 2
# Covers the real layout observed on this operator's machine: standalone
# repos directly under home (depth 1: ai-controller, master-ai-cli, ...)
# AND repos nested one level inside a container folder (depth 2:
# projects/tahuti, workspace/ai-controller, repos/no-mistakes, ...).
# Deeper than that risks scanning into a repo's own vendored/nested
# checkouts; stops descending the moment a .git is found at any level.


def _find_git_repos_under_home() -> list[Path]:
    home = Path.home()
    found: list[Path] = []
    if (home / ".git").is_dir():
        found.append(home)
    for root, dirs, _files in os.walk(home):
        rel = Path(root).relative_to(home)
        depth = 0 if str(rel) == "." else len(rel.parts)
        # Prune hidden dirs (dotfile noise, same convention as list_dir)
        # and anything past the depth cap, before os.walk descends further.
        # Sorted for deterministic, reproducible results -- os.walk's raw
        # order depends on filesystem/inode ordering, not anything
        # meaningful to the operator.
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and depth < MAX_COMMIT_WALK_DEPTH)
        if depth == 0:
            continue  # home itself already handled above
        if (Path(root) / ".git").is_dir():
            found.append(Path(root))
            dirs[:] = []  # a repo's own subdirs aren't separate repos to scan
        if len(found) >= MAX_COMMIT_REPOS_SCANNED:
            break
    return found


def _check_commits_tool(repo: str = "", days: int = 14, limit: int = 10) -> dict:
    """Read-only `git log` across the operator's repos -- never commits,
    pushes, or modifies anything. Added 2026-11-01: "tell me my recent
    commits" previously had no tool to answer with at all, so the model
    correctly said it needed approval but had no way to actually file
    one -- no box ever appeared. This is read-only and safe, same
    category as check_email, so it just runs immediately like that does.
    """
    try:
        days = max(1, min(90, int(days)))
    except Exception:
        days = 14
    try:
        limit = max(1, min(50, int(limit)))
    except Exception:
        limit = 10

    if repo:
        p = Path(repo)
        if not p.is_absolute():
            p = Path.home() / p
        if not _is_under_home(p):
            return {"ok": False, "error": "path outside home directory refused"}
        if not (p / ".git").is_dir():
            return {"ok": False, "error": f"not a git repo: {repo}"}
        candidates = [p]
    else:
        candidates = _find_git_repos_under_home()
        if not candidates:
            return {"ok": True, "output": "No git repositories found under your home directory."}

    since = f"--since={days} days ago"

    def _log_one(repo_dir: Path) -> Optional[str]:
        try:
            proc = subprocess.run(
                ["git", "-C", str(repo_dir), "log", since, f"-{limit}",
                 "--pretty=format:%h %ad %s", "--date=short"],
                capture_output=True, text=True, timeout=5,
            )
        except Exception:
            return None
        if proc.returncode != 0:
            return None
        lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
        if not lines:
            return None
        # Relative-to-home path, not bare .name -- this operator has
        # multiple distinct repos sharing the same basename (both
        # ~/ai-controller and ~/projects/ai-controller are real, separate
        # clones), and bare .name made both show up as an identical,
        # unanswerable "## ai-controller" header. Falls back to "~" for
        # home itself, which has no meaningful relative path.
        try:
            rel = repo_dir.relative_to(Path.home())
            label = "~" if str(rel) == "." else str(rel)
        except ValueError:
            label = str(repo_dir)
        return f"## {label}\n" + "\n".join(f"  {ln}" for ln in lines)

    # Parallel, not sequential -- with MAX_COMMIT_REPOS_SCANNED raised to
    # 60, running `git log` one repo at a time (even at a quick 5s
    # timeout each) could add up; this keeps wall-clock latency close to
    # the single slowest repo instead of the sum of all of them.
    sections: list[str] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for result in pool.map(_log_one, candidates):
            if result:
                sections.append(result)

    if not sections:
        return {
            "ok": True,
            "output": f"No commits in the last {days} day(s) across {len(candidates)} repo(s) checked.",
        }
    return {"ok": True, "output": "\n\n".join(sections)}


def _request_approval_tool(goal_id: str, action: str, reason: str) -> dict:
    """Raise the operator's real approval card for one gated action.

    The model's only seam to the box: instead of dead-ending in a text
    refusal, it calls this and the actual card appears. Fail-closed on
    every path -- deny, timeout, and headless all come back {"ok": False}
    so the model can only ever report the operator's real decision.

    A "session" verdict deliberately does NOT write a persistent grant:
    the operator's card offers session and always as separate buttons but
    _present_approval maps both to "session", so persisting here would
    silently turn a session approval into a permanent one. Until that
    mapping is fixed, this tool treats session as approved-for-this-call
    only and never writes the grants file.
    """
    action = (action or "").strip()
    reason = (reason or "").strip()
    if not action:
        return {"ok": False, "error": "action required: name the gated action"}
    words = re.findall(r"[a-z]+", action.lower())
    # Same vocabulary as the goal-level gate: an APPROVAL_VERB in the
    # phrase wins so a grant here covers the same scope the goal gate
    # would have used; otherwise the first word labels the pattern.
    verb_hits = [w for w in words if w in APPROVAL_VERBS]
    verb = verb_hits[0] if verb_hits else (words[0] if words else "action")
    pattern = f"tahuti:{verb}:background"
    if _is_granted(pattern):
        return {"ok": True, "decision": "already-granted",
                "note": f"a permanent grant already covers {pattern}; "
                        "proceed with the tools you actually have"}
    logger.info("model requested approval for %s (goal %s)", pattern, goal_id)
    outcome = _present_approval(
        pattern,
        action,
        why=reason or "the model asked for the operator's go-ahead",
        how="Tahuti goal runner (model-requested approval)",
    )
    if outcome == "once":
        return {"ok": True, "decision": "once",
                "note": "the operator approved this one request; proceed "
                        "only with the tools you actually have this turn, "
                        "and say so plainly if none can perform the action"}
    if outcome == "session":
        return {"ok": True, "decision": "session",
                "note": "the operator approved it for this session; proceed "
                        "only with the tools you actually have this turn, "
                        "and say so plainly if none can perform the action"}
    if outcome == "deny":
        return {"ok": False, "decision": "deny",
                "note": "the operator denied it; do not attempt the action"}
    return {"ok": False, "decision": "unavailable",
            "note": "no approval card could be shown, so nothing was "
                    "approved; do not attempt the action"}


def _execute_file_tool(goal_id: str, call: dict, allowed_names: set) -> dict:
    """Execute one tool call and return a JSON-serializable result.

    allowed_names is the set of tool names actually declared in THIS
    request's "tools" array (fast-chat/fast-lookup declare a reduced
    set for latency). Without this check this function dispatched any
    recognized tool name regardless of what was declared -- the model
    reliably hallucinates calls to read_file/list_dir even when "tools"
    was sent as an empty list (reproduced live 2026-11-01, twice,
    identical hallucinated calls with empty arguments both times), so
    the per-route tool restriction was enforced in name only. Refusing
    here, not just omitting the schema, is the actual enforcement point.
    """
    name = call.get("name")
    if name not in allowed_names:
        return {"ok": False, "error": f"tool '{name}' not available for this request"}
    try:
        args = json.loads(call.get("arguments", "{}"))
    except Exception:
        return {"ok": False, "error": "invalid JSON arguments"}

    if name == "read_file":
        result = _read_file_tool(
            args.get("path", ""), args.get("offset", 1), args.get("limit")
        )
    elif name == "list_dir":
        result = _list_dir_tool(args.get("path", ""), args.get("show_hidden", False))
    elif name == "write_file":
        result = _write_file_tool(
            goal_id,
            args.get("path", ""),
            args.get("content", ""),
            args.get("reason", ""),
        )
    elif name == "open_url":
        result = _open_url_tool(args.get("url", ""))
    elif name == "open_app":
        result = _open_app_tool(args.get("name", ""))
    elif name == "check_email":
        result = _check_email_tool(args.get("days", 2), args.get("unread", True))
    elif name == "check_commits":
        result = _check_commits_tool(
            args.get("repo", ""), args.get("days", 14), args.get("limit", 10)
        )
    elif name == "request_approval":
        result = _request_approval_tool(
            goal_id, args.get("action", ""), args.get("reason", "")
        )
    else:
        result = {"ok": False, "error": f"unknown tool: {name}"}

    # Single choke point so every tool (current and future) lands in
    # Sensei's tmux thread the same way -- see module comment above
    # _write_to_agent_thread. Best-effort: never lets a thread-write
    # problem affect the actual answer returned to the model. Falls back
    # to a disposable popup only when Sensei's own tmux session isn't up
    # at all -- otherwise the result would reach nowhere visible.
    try:
        landed = _write_to_agent_thread(name, args, result)
        if not landed:
            _pop_text_terminal(name, _format_tool_result_for_thread(name, args, result))
    except Exception as exc:
        logger.warning("agent-thread dispatch failed: %s", exc)
    return result


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
        # Match against the camelCase-split transcript, not the raw one --
        # see _split_camel_case. Real live bug 2026-11-01: a spoken "open
        # facebook.com" came back from STT as "OpenFacebook.com." with no
        # space, so _OPEN_TRIGGER_RE's word-boundary match never fired,
        # open_url/open_app never got offered, and the model was left
        # honestly saying "needs approval" with literally nothing to call.
        matching_text = _split_camel_case(transcript)
        if route == "fast-lookup":
            tools: list[dict] = [{"type": "web_search"}] if any(
                w in matching_text.lower() for w in SIMPLE_LOOKUP_TRIGGERS
            ) or "?" in transcript else []
            # include email tool if transcript asks email (still fast)
            if _EMAIL_TRIGGER_RE.search(matching_text):
                tools.append(EMAIL_TOOL)
            if _COMMIT_TRIGGER_RE.search(matching_text):
                tools.append(COMMIT_TOOL)
        else:  # fast-chat
            tools = []
            if _EMAIL_TRIGGER_RE.search(matching_text):
                tools = [EMAIL_TOOL]
            if _COMMIT_TRIGGER_RE.search(matching_text):
                tools.append(COMMIT_TOOL)
        # open_url/open_app back in when actually asked -- cheap single-shot
        # calls, not what the fast lanes are too thin for. Without this the
        # model hallucinates having opened something it was never given the
        # tool to open (see OPEN_TOOLS comment above).
        if _OPEN_TRIGGER_RE.search(matching_text):
            tools.extend(_tools_with_live_open_app(OPEN_TOOLS))
        # The approval seam must exist on EVERY route, including the thin
        # fast lanes: a text refusal with no card is exactly the dead end
        # this tool exists to remove.
        tools.append(REQUEST_APPROVAL_TOOL)
    else:
        tools = _all_tools(transcript)

    instructions = SAFETY_PROMPT_HEAD + _tools_sentence(tools) + " " + SAFETY_PROMPT_TAIL
    # Only mention pin names when open_app is actually offered this round --
    # same bug class as the tools sentence above: naming an app you can't
    # actually launch this turn invites the same kind of fabricated claim.
    if any(t.get("name") == "open_app" for t in tools):
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

    allowed_tool_names = {t.get("name") for t in tools if t.get("name")}
    try:
        # shorter timeout for fast routes
        timeout = 30.0 if route.startswith("fast-") else MODEL_TIMEOUT
        round_num = 0
        # Absolute stop regardless of what the model keeps sending -- covers
        # the model still emitting a function_call even after tools=[] (the
        # same hallucinated-call behavior seen elsewhere in this file), so
        # the loop can never spin forever no matter what the response shape is.
        hard_round_ceiling = MAX_TOOL_ROUNDS + 2
        with httpx.Client(timeout=timeout) as client:
            while True:
                round_num += 1
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
                if not function_calls or round_num >= hard_round_ceiling:
                    break

                outputs = []
                approval_granted = False
                for call in function_calls:
                    result = _execute_file_tool(goal_id, call, allowed_tool_names)
                    if (call.get("name") == "request_approval"
                            and result.get("ok") is True):
                        approval_granted = True
                    outputs.append(
                        {
                            "type": "function_call_output",
                            "call_id": call.get("call_id", "unknown"),
                            "output": json.dumps(result),
                        }
                    )

                # Approval escalation (2026-11-01): the operator's 'yes' must
                # unlock REAL work, not just permission. The thin fast lanes
                # strip list_dir/read_file, so an approved "look in my skills"
                # dead-ended in "approved — but I don't have a file-listing
                # tool this turn" (observed live in the operator's own thread,
                # twice). An approved request_approval now re-arms the
                # continuation with the FULL safe toolset and a fresh round
                # budget, and tells the model to do the work and SHOW it:
                # where it looked, what it found, what it did. No new powers
                # are granted -- shell/installs/deletes still have no
                # executor on any route; escalation only restores the tools
                # the safe routes already have.
                if approval_granted:
                    tools = _all_tools(transcript)
                    round_num = 0
                    logger.info(
                        "approval escalation: full safe toolset re-armed "
                        "for goal %s", goal_id)
                    outputs.append({
                        "role": "user",
                        "content": (
                            "The operator APPROVED this request. Your tool "
                            "set has been re-armed with the full safe toolset "
                            "for this request (read_file, list_dir, write_file, "
                            "open_url/open_app, web_search where offered) and "
                            "your search budget is reset. Do the work NOW, "
                            "then show your work: report exactly where you "
                            "looked, what you found, and what you did. If "
                            "you still cannot complete it with these tools, "
                            "say that plainly with the real reason."),
                    })

                # Past MAX_TOOL_ROUNDS, stop offering tools -- forces the
                # model to write a real text answer from what it already
                # found instead of trying yet another candidate path.
                #
                # allowed_tool_names MUST be recomputed here, not left as
                # the original full set -- found live 2026-11-01 testing a
                # vague "review the voice bridge code" request: round 5 and
                # 6 both sent tools=[] to the API, but the model still
                # hallucinated real list_dir/read_file calls anyway (same
                # phenomenon documented on _execute_file_tool), and because
                # this set was never narrowed to match, the executor let
                # every one of them through -- the round cap was enforced
                # in the request payload only, never at the actual
                # execution point, so it bounded nothing.
                next_tools = tools if round_num < MAX_TOOL_ROUNDS else []
                allowed_tool_names = {t.get("name") for t in next_tools if t.get("name")}
                if tools and not next_tools:
                    # Just-in-time instruction at the exact moment tools get
                    # withheld, instead of relying on the static honesty
                    # rule stated once at the very start of the request
                    # (many tokens and several rounds ago by now). Found
                    # live 2026-11-01: without this, once calls started
                    # getting refused post-cap, the model didn't admit it
                    # ran out of search budget -- it fabricated a DIFFERENT,
                    # plausible-sounding but false excuse instead ("my file
                    # reader hit a limit on that file size," which never
                    # happened in that trace). The existing honesty rule
                    # only covers claiming false SUCCESS; this covers
                    # claiming a false REASON for a real failure.
                    outputs.append({
                        "role": "user",
                        "content": (
                            "You've used your search/tool budget for this "
                            "request -- no further tool call will work from "
                            "here, any you try will be refused. Write your "
                            "real answer now from only what you actually "
                            "found above. If you didn't find or finish what "
                            "was asked, say exactly that -- you ran out of "
                            "search attempts -- do NOT invent a different "
                            "reason (a file-size limit, an API error, a "
                            "permission issue, anything else) that did not "
                            "actually happen in this conversation."
                        ),
                    })
                payload = {
                    "model": model,
                    "previous_response_id": response_id,
                    "input": outputs,
                    "tools": next_tools,
                }
                if max_tokens:
                    payload["max_output_tokens"] = max_tokens

            text = _extract_responses_text(data)
            if not text:
                text = "I thought about it, but didn't have a reply."
            elapsed_s = time.time() - t0
            elapsed = f"{elapsed_s:.1f}s"
            logger.info("model %s route %s elapsed %.2fs", model, route, elapsed_s)
            _write_answer_to_thread(transcript, route, text)
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
        empty_text = "I couldn't get an answer from either model just now -- try again in a moment."
        _write_answer_to_thread(transcript, route, empty_text)
        return empty_text, f"{FALLBACK_MODEL} (empty)", elapsed, elapsed_s, route
    elapsed_s = time.time() - t0
    elapsed = f"{elapsed_s:.1f}s"
    logger.info("Free fallback (%s) answered", FALLBACK_MODEL)
    _write_answer_to_thread(transcript, route, text)
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
    words = set(re.findall(r"[a-z]+", _split_camel_case(transcript).lower()))
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
