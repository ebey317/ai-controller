"""sensei_model.py — answer with the model SELECTED IN SENSEI (2026-11-03).

Operator spec: "the model that i select in sensei is the model i should be
using." Sensei stores its selection as `provider::model` in
~/.master_ai_active_model (empty / "auto" = Sensei's own router picks).

This does not import Sensei's agent loop (tangled with privacy prompts, TTY
assumptions and tombstones — wrong for a headless daemon). It reads the same
pin, calls that provider's OpenAI-compatible endpoint with the SAME keys
(~/.master_ai_keys), and runs Tahuti's tool loop in chat-completions format.

Pure helpers (pin parsing, key lookup, tool conversion) are separate from the
network loop so they can be tested without a network.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Callable, Optional

import httpx

ACTIVE_MODEL_FILE = Path.home() / ".master_ai_active_model"
KEYS_FILE = Path.home() / ".master_ai_keys"

_ZEN = "https://opencode.ai/zen/v1/chat/completions"
# provider prefix (as written in the pin) -> (endpoint, [key env names])
PROVIDERS: dict[str, tuple[str, list[str]]] = {
    "local": ("http://localhost:11434/v1/chat/completions", []),
    "ollama": ("http://localhost:11434/v1/chat/completions", []),
    "opencode": (_ZEN, []),  # Zen: keyless, needs a session header
    "opencode-free": (_ZEN, []),
    "opencode-go": (
        "https://opencode.ai/zen/go/v1/chat/completions",
        ["OPENCODE_GO_API_KEY", "OPENCODE_API_KEY"],
    ),
    "ollama-cloud": ("https://ollama.com/v1/chat/completions", ["OLLAMA_API_KEY"]),
    "openrouter": (
        "https://openrouter.ai/api/v1/chat/completions",
        ["OPENROUTER_API_KEY"],
    ),
    "nvidia": (
        "https://integrate.api.nvidia.com/v1/chat/completions",
        ["NVIDIA_API_KEY"],
    ),
    "cerebras": ("https://api.cerebras.ai/v1/chat/completions", ["CEREBRAS_API_KEY"]),
    "poolside": (
        "https://inference.poolside.ai/v1/chat/completions",
        ["POOLSIDE_API_KEY"],
    ),
    "qwen": (
        "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/chat/completions",
        ["QWEN_TOKENPLAN_API_KEY"],
    ),
}


class ProviderError(RuntimeError):
    """The pinned provider could not give an answer (caller falls back)."""


# --------------------------------------------------------------------------
# pin + keys
# --------------------------------------------------------------------------
def active_pin(path: Path = ACTIVE_MODEL_FILE) -> Optional[tuple[str, str]]:
    """(provider, model) from Sensei's pin, or None when Sensei is on AUTO."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except Exception:
        return None
    if not text or text.lower() in ("auto", "none") or "::" not in text:
        return None
    provider, model = text.split("::", 1)
    provider, model = provider.strip().lower(), model.strip()
    return (provider, model) if provider and model else None


def _provider_entry(provider: str) -> Optional[tuple[str, list[str]]]:
    if provider in PROVIDERS:
        return PROVIDERS[provider]
    if provider.startswith("qwen"):
        return PROVIDERS["qwen"]
    return None


def supported(provider: str) -> bool:
    return _provider_entry(provider) is not None


# Sensei keeps most keys in ~/.master_ai_keys, but OLLAMA_API_KEY (Ollama Cloud)
# and the Zen session live in ~/.hermes/.env — read both, like Sensei does.
_KEY_FILES = (KEYS_FILE, Path.home() / ".hermes" / ".env")


def _read_keys_file() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in _KEY_FILES:
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                m = re.match(r"^(?:export\s+)?([A-Z0-9_]+)=(.*)$", line)
                if m and m.group(2).strip().strip('"').strip("'"):
                    out.setdefault(m.group(1), m.group(2).strip().strip('"').strip("'"))
        except Exception:
            pass
    return out


def find_key(names: list[str]) -> str:
    """First non-empty value among `names`: process env, then Sensei's keys file."""
    file_keys = None
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
        if file_keys is None:
            file_keys = _read_keys_file()
        if file_keys.get(n):
            return file_keys[n]
    return ""


def _zen_session() -> str:
    val = os.environ.get("OPENCODE_SESSION", "").strip()
    if val:
        return val
    try:
        for ln in (Path.home() / ".hermes" / ".env").read_text().splitlines():
            ln = ln.strip()
            if ln.startswith("export OPENCODE_SESSION="):
                v = ln.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    except Exception:
        pass
    return f"tahuti-{uuid.uuid4().hex[:12]}"


def describe(pin: tuple[str, str]) -> str:
    return f"{pin[0]}::{pin[1]}"


# --------------------------------------------------------------------------
# tools: Tahuti keeps them in Responses format; chat-completions wants another shape
# --------------------------------------------------------------------------
def to_chat_tools(tools: list[dict]) -> list[dict]:
    """Function tools only. Native/provider-hosted tools (e.g. {"type":"web_search"},
    which only Meta's endpoint understands) cannot be offered to other models."""
    out = []
    for t in tools or []:
        if t.get("type") == "function" and t.get("name"):
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("parameters")
                        or {"type": "object", "properties": {}},
                    },
                }
            )
    return out


# --------------------------------------------------------------------------
# the call
# --------------------------------------------------------------------------
def run(
    pin: tuple[str, str],
    system: str,
    history: list[dict],
    user: str,
    tools: list[dict],
    execute: Callable[[dict], dict],
    max_rounds: int = 6,
    timeout: float = 90.0,
    max_tokens: int = 2048,
) -> str:
    """One goal answered by the pinned model, with Tahuti's tool loop.

    `execute(call)` takes {"name","arguments"(JSON str),"call_id"} and returns a
    JSON-able dict — i.e. Tahuti's own _execute_file_tool, so every tool keeps
    its approval gates. Raises ProviderError on anything that should trigger the
    Muse fallback (no key, HTTP error, empty answer)."""
    provider, model = pin
    entry = _provider_entry(provider)
    if entry is None:
        raise ProviderError(f"provider '{provider}' is not wired for voice yet")
    url, key_names = entry
    headers = {"Content-Type": "application/json"}
    if key_names:
        key = find_key(key_names)
        if not key:
            raise ProviderError(
                f"no API key for {provider} (looked for {', '.join(key_names)})"
            )
        headers["Authorization"] = f"Bearer {key}"
    if "opencode.ai/zen/" in url:  # Zen AND Go both need the session header
        headers["x-opencode-session"] = _zen_session()

    chat_tools = to_chat_tools(tools)
    messages: list[dict] = [
        {"role": "system", "content": system},
        *history,
        {"role": "user", "content": user},
    ]
    with httpx.Client(timeout=timeout) as client:
        for round_num in range(max_rounds + 1):
            body: dict = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
            }
            if chat_tools and round_num < max_rounds:
                body["tools"] = chat_tools
                body["tool_choice"] = "auto"
            try:
                r = client.post(url, headers=headers, json=body)
            except Exception as exc:
                raise ProviderError(f"{type(exc).__name__}: {exc}") from exc
            if r.status_code >= 400:
                raise ProviderError(f"HTTP {r.status_code}: {r.text[:160]}")
            try:
                msg = r.json()["choices"][0]["message"]
            except Exception as exc:
                raise ProviderError(f"unreadable reply: {r.text[:160]}") from exc
            calls = msg.get("tool_calls") or []
            if not calls:
                text = (msg.get("content") or "").strip()
                if not text:
                    raise ProviderError("empty reply")
                return text
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.get("content") or "",
                    "tool_calls": calls,
                }
            )
            for c in calls:
                fn = c.get("function") or {}
                result = execute(
                    {
                        "name": fn.get("name"),
                        "arguments": fn.get("arguments") or "{}",
                        "call_id": c.get("id", ""),
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": c.get("id", ""),
                        "content": json.dumps(result),
                    }
                )
            if round_num + 1 >= max_rounds:  # out of budget: force a real answer
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "You've used your tool budget. Write your real answer now from only "
                            "what you actually found above; if you did not finish, say exactly that."
                        ),
                    }
                )
    raise ProviderError("tool loop ended without an answer")
