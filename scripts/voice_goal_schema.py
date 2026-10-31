"""Shared goal schema + intent classification for the voice bridge and Tahuti runner.

Keeps the writer (voice_bridge.py) and the reader/writer (tahuti_goal_runner.py)
on the same contract so handoffs never crash on missing fields.
"""
from __future__ import annotations

import re
import time
import uuid
from typing import Optional

# Handoff-approved routing: task verbs create an immediate goal file for the
# Tahuti headless runner; chat/lookup continue to the LLM for a direct answer.
TASK_VERBS = {
    "write", "build", "fix", "open", "launch",
    "create", "run", "start", "stop", "restart",
    "edit", "make", "generate", "deploy", "install",
    "update", "delete", "remove", "send", "move", "copy",
}
# NOTE 2026-10-30: "open"/"launch" route again — the runner now executes them
# through gated open_url/open_app tools, so the model acts instead of punting
# to approvals. "operate" stays chat (unbounded desktop control, Phase 3).

_CHAT_WORDS = {
    "what", "who", "when", "where", "why", "how", "is", "are", "was", "were",
    "tell", "explain", "describe", "define", "look", "search", "chat", "talk",
    "hello", "hi", "hey", "thanks", "thank",
}

# Deterministic intent routing (2026-10-30): some requests resolve to a
# fixed handler, no model guessing. Email: "check/read/show ... email/mail/
# inbox" is always a task (type "email") — "check the weather" stays chat
# because it has no email word. Show: "show me your work" opens the last
# response file deterministically (type "show") — zero tokens.
_EMAIL_WORDS = {"email", "emails", "mail", "inbox", "inboxes"}
_EMAIL_VERBS = {"check", "read", "show", "see", "get", "fetch", "look"}
_SHOW_WORDS = {"work", "result", "results", "answer"}
_SHOW_VERBS = {"show", "display", "open"}
# Leading words that mark a spoken question even without a trailing "?".
# NOTE 2026-10-30: can/could/will/would are NOT here — polite commands
# ("can you open facebook") must reach the runner's open tools, and without
# a task verb they still fall through to chat below. "do/does/did" stay:
# "do you write poetry" is a question, not a task.
_QUESTION_LEADERS = {
    "what", "who", "when", "where", "why", "how", "is", "are", "was", "were",
    "do", "does", "did", "should", "shall",
    "am", "may", "might",
}

_NEGATION_TOKENS = {
    "not", "no", "never", "none", "nothing", "nobody", "nowhere",
    "don't", "doesn't", "didn't", "won't", "can't", "shouldn't",
    "wouldn't", "couldn't", "isn't", "aren't", "wasn't", "weren't",
    "hasn't", "haven't", "hadn't",
}

GOAL_REQUIRED_FIELDS = ["id", "transcript", "type", "time", "source"]


def _tokens(transcript: str) -> list[str]:
    """Lowercase word-ish tokens; keep contractions like don't."""
    return re.sub(r"[^a-z' ]", " ", transcript.lower()).split()


def classify_transcript(transcript: str) -> str:
    """Return 'task' only for a genuine imperative/request with a task verb.

    Questions ("How do I install software?") and negations
    ("Do not delete anything") are always chat, even if they contain a task verb.
    """
    if not transcript:
        return "chat"
    stripped = transcript.strip()
    tokens = _tokens(stripped)
    if not tokens:
        return "chat"

    # Questions: explicit trailing question mark, or a question-leading word.
    if stripped.endswith("?") or tokens[0] in _QUESTION_LEADERS:
        return "chat"

    # Negations: anything with a negation marker is an instruction to *not* act.
    if any(tok in _NEGATION_TOKENS for tok in tokens):
        return "chat"

    # Email intent: deterministic, resolved before anything else. "Check my
    # emails" must not depend on the model guessing — it maps to the email
    # handler. Requires an email word AND an email verb so "email me the
    # weather" style chatter without one stays on the normal path.
    tokset = set(tokens)
    if tokset & _EMAIL_WORDS and tokset & (_EMAIL_VERBS | TASK_VERBS):
        return "task"

    # Show intent: "show me your work" — deterministic file open, no model.
    if tokset & _SHOW_WORDS and tokset & _SHOW_VERBS:
        return "task"

    if any(tok in TASK_VERBS for tok in tokens):
        return "task"

    # Short utterances that start with a chat word default to chat.
    if len(tokens) <= 4 and tokens[0] in _CHAT_WORDS:
        return "chat"

    return "chat"


def task_type(transcript: str) -> Optional[str]:
    """Return the intent type: 'email' for email checks, else first task verb."""
    # Skip task verbs inside negated or question contexts.
    if classify_transcript(transcript) != "task":
        return None
    toks = _tokens(transcript)
    tokset = set(toks)
    if tokset & _EMAIL_WORDS and tokset & (_EMAIL_VERBS | TASK_VERBS):
        return "email"
    if tokset & _SHOW_WORDS and tokset & _SHOW_VERBS:
        return "show"
    for tok in toks:
        if tok in TASK_VERBS:
            return tok
    return None


def build_goal_payload(transcript: str, effort: Optional[str] = None) -> dict:
    """Build a goal file payload that satisfies the shared schema."""
    payload = {
        "id": uuid.uuid4().hex,
        "transcript": transcript,
        "type": task_type(transcript) or "task",
        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": "voice_bridge",
    }
    # Selective thinking (Master AI vocabulary): none/low/medium/high/xhigh.
    # Runner honors it when valid; otherwise its own defaults apply.
    if effort in ("none", "low", "medium", "high", "xhigh"):
        payload["effort"] = effort
    return payload
