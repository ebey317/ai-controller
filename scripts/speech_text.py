"""Single source of truth for what Tahuti SAYS (2026-11-03, operator spec:
"read the whole answer").

Both the voice bridge (what is voiced) and the goal runner (what the avatar
box shows) import spoken_form(), so the words on screen are always the words
heard. Change the rule here, nowhere else.
"""
from __future__ import annotations

import html
import re

# Upper bound on one spoken reply. Long enough for a whole short answer or a
# list, short enough that a runaway reply is not read for minutes.
MAX_SPOKEN_CHARS = 1200

_SPACES = re.compile(r"[ \t]+")

# Real markdown only. The old rule deleted EVERY `_ * # >` which turned
# "tahuti_goal_runner.py" into "tahutigoalrunner.py" and ate "&gt;" (review
# finding, 2026-11-04). Identifiers, file names and URLs must survive.
_MD_PATTERNS = [
    (re.compile(r"`+"), ""),                                        # code ticks
    (re.compile(r"^\s{0,3}#{1,6}\s+", re.M), ""),                    # "## Heading"
    (re.compile(r"^\s{0,3}>\s?", re.M), ""),                         # "> quote"
    (re.compile(r"^\s{0,3}[*+]\s+", re.M), ""),                      # "* bullet"
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),                           # **bold**
    (re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])"), r"\1"),   # *italic*
    (re.compile(r"(?<![\w_])__(?!\s)([^_\n]+?)(?<!\s)__(?![\w.])"), r"\1"),  # __bold__
    (re.compile(r"(?<![\w_])_(?!\s)([^_\n./]+?)(?<!\s)_(?![\w.])"), r"\1"),  # _italic_
]


def _strip_markdown(raw: str) -> str:
    text = html.unescape(raw)
    for pat, repl in _MD_PATTERNS:
        text = pat.sub(repl, text)
    return text


def spoken_form(text: str, limit: int = MAX_SPOKEN_CHARS) -> str:
    """The whole answer, flattened for the voice.

    - every line is kept (the old rule spoke only the first line)
    - markdown symbols that would be read aloud as noise are dropped
    - lines are joined with a pause (". ") unless they already end in
      sentence punctuation, so list items don't run together
    - if over `limit`, cut at the last sentence/line end that fits
    """
    if not text:
        return ""
    lines = []
    for raw in _strip_markdown(text).splitlines():
        line = _SPACES.sub(" ", raw).strip()
        if line:
            lines.append(line)
    out = ""
    for line in lines:
        if out and out[-1] not in ".!?:;,":
            out += "."
        out += (" " if out else "") + line
    if len(out) <= limit:
        return out
    cut = out[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end > limit // 2:
        return cut[: end + 1].strip()
    # No sentence end in reach: cut at a WORD boundary, never mid-word.
    space = cut.rfind(" ")
    return (cut[:space] if space > limit // 2 else cut).strip() + "…"
