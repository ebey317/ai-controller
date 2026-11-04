"""sensei_slash.py — drive the LIVE Sensei TUI from Tahuti (2026-11-03).

Operator spec: "the headless agent needs to run just like a TUI" — say a slash
command by voice and it runs in the REAL Sensei loop, with Sensei's real state
(active model, mode, history), not a lookalike. So this does not re-implement
commands: it recognises a spoken slash command, makes sure Sensei is actually
running in its tmux session, types the command in, waits for the output to
settle, and hands back what Sensei printed.

Source of truth for what commands exist: master-ai-cli/command_registry.py,
loaded fresh by file path (never copied), so new commands appear automatically.

Pure/testable pieces (parse, registry, diffing) are separate from the tmux I/O.
"""
from __future__ import annotations

import difflib
import importlib.util
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

REGISTRY_PATH = Path.home() / "master-ai-cli" / "command_registry.py"
AUTOSTART = Path.home() / "scripts" / "master_ai_autostart.sh"

# Commands that end the loop, wipe state, or execute a pending plan. These go
# through the operator's approval card instead of running on a spoken word.
_GATED_FIRST_WORDS = {"x", "exit", "quit", "restart", "clear", "forget", "go", "unload"}
_GATED_FULL = {"git commit", "hooks disable"}

_SPOKEN_SYMBOLS = {"underscore": "_", "colon": ":"}


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------
_reg_cache: dict = {"mtime": 0.0, "rows": []}


def load_commands() -> list[tuple]:
    """[(name, hint, needs_arg, section), ...] from Sensei's own registry."""
    try:
        mtime = REGISTRY_PATH.stat().st_mtime
        if mtime != _reg_cache["mtime"]:
            spec = importlib.util.spec_from_file_location("_sensei_command_registry", REGISTRY_PATH)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            _reg_cache.update(mtime=mtime, rows=list(mod.COMMANDS))
    except Exception:
        pass
    return _reg_cache["rows"]


def _base(name: str) -> str:
    return name.rstrip(":").strip()


# "slash help" / "slash how do I use this" open the paged command guide: Sensei's own
# /help page is taller than its window, so a card can only ever show its tail.
_GUIDE_PHRASES = {"help", "help me", "how do i use this", "how do i use it", "how does this work",
                  "what can you do", "what can i say", "how do i use tahuti", "how do i use sensei",
                  "guide", "commands guide"}

# Spoken shortcuts: "slash plan" means Sensei's `mode plan`, and so on.
_ALIASES = {"plan": "mode plan", "plan mode": "mode plan",
            "review": "mode review", "review mode": "mode review",
            "auto mode": "mode auto", "local mode": "mode local",
            "connected mode": "mode connected"}

# Bare names that must NOT become a button menu (they have their own flow).
_NO_MENU = {"model", "master", "agent", "reason", "reasoning"}
_MENU_BARE_LABEL = {"mode": "Show current mode"}
_MENU_MAX_ITEMS = 6


def menu_for(base: str) -> Optional[dict]:
    """A button menu for a command that has ready-made sub-options in Sensei's
    registry ("mode" -> plan/review/auto/...; "tts" -> on/off). Built from the
    registry so a new sub-command shows up with no code change. None if the
    command has fewer than two zero-input sub-options."""
    base = base.strip().lower()
    if not base or " " in base or base in _NO_MENU:
        return None
    items: list[tuple[str, str, str]] = []   # (label, command to send, spoken phrase)
    for name, _hint, needs_arg, _section in load_commands():
        words = _base(name).lower().split()
        if needs_arg or len(words) != 2 or words[0] != base:
            continue
        sub = words[1]
        items.append((sub.replace("_", " ").title(), "/" + " ".join(words), f"slash {' '.join(words)}"))
    has_bare = any(_base(n).lower() == base for n, *_ in load_commands())
    if len(items) < (1 if not has_bare else 2):
        return None
    if base in _MENU_BARE_LABEL:
        items.insert(0, (_MENU_BARE_LABEL[base], "/" + base, f"slash {base}"))
    return {"base": base, "title": f"/{base}", "items": items[:_MENU_MAX_ITEMS]}


def is_gated(name: str) -> bool:
    b = _base(name).lower()
    words = b.split()
    return b in _GATED_FULL or (bool(words) and words[0] in _GATED_FIRST_WORDS)


# --------------------------------------------------------------------------
# parsing a spoken/typed transcript
# --------------------------------------------------------------------------
# "show me the docker commands" is a question for the model, NOT a request for
# Sensei's list (review finding, 2026-11-04): the word has to be "slash", or the
# whole utterance has to be just "(list|show) (my|all) commands".
_LIST_RE = re.compile(
    r"^\s*(?:/commands?|"
    r"(?:list|show|read|tell me|what are|get|give me)\b[^.?!]{0,30}\bslash commands?|"
    r"(?:list|show)(?:\s+me)?(?:\s+all)?(?:\s+(?:the|my|your))?\s+commands?|"
    r"slash commands?|slash|/)\s*[.?!]*\s*$", re.I)

# Say "slash models" (or "change model") -> the interactive selector card, not
# a one-shot text dump. Sensei's own /model is an interactive picker.
_PICKER_PHRASES = {"models", "model", "model selector", "model picker", "model list",
                   "change model", "change models", "select model", "select models",
                   "switch model", "switch models", "pick model", "pick a model"}
_RUN_RE = re.compile(r"^\s*(?:/|slash(?:\s+command)?[\s,:]+)\s*(.+?)\s*$", re.I)


def _normalise(text: str) -> str:
    s = (text or "").lower()
    for word, ch in _SPOKEN_SYMBOLS.items():
        s = re.sub(rf"\b{word}\b", ch, s)
    s = re.sub(r"[^a-z0-9_:.\-\s]", " ", s)  # keep : . _ - for qwen2.5vl:3b etc.
    s = re.sub(r"\s+", " ", s).strip(" .,;")
    return s


def parse(transcript: str) -> Optional[dict]:
    """None if this is not a slash-command utterance (falls through to the model).

    {"kind": "list"}
    {"kind": "run", "name", "base", "arg", "send", "gated"}
    {"kind": "unknown", "said", "suggest": [...]}
    """
    t = (transcript or "").strip()
    if not t:
        return None
    if _LIST_RE.match(t):
        return {"kind": "list"}
    m = _RUN_RE.match(t)
    if not m:
        return None
    rest = _normalise(m.group(1))
    if not rest:
        return {"kind": "list"}  # just said "slash"
    if rest in _PICKER_PHRASES:
        return {"kind": "picker", "what": "model"}
    if rest in _GUIDE_PHRASES:
        return {"kind": "list"}
    if rest in ("sensei help", "help pager", "sensei help pager"):
        return {"kind": "run", "name": "help", "base": "help", "arg": "", "send": "/help",
                "gated": False}
    if rest in ("readme", "read me", "open readme", "open the readme", "open the read me"):
        return {"kind": "readme"}
    m2 = re.match(r"^(?:turn\s+)?(?:the\s+)?(?:readme\s+)?(?:reminders?|nudges?|tips)\s*(on|off)?$", rest)
    if m2 and rest != "tips":
        return {"kind": "reminders", "on": None if not m2.group(1) else m2.group(1) == "on"}
    if rest in ("turn off reminders", "turn off the reminders", "turn on reminders", "turn on the reminders"):
        return {"kind": "reminders", "on": " on " in f" {rest} "}
    rest = _ALIASES.get(rest, rest)
    menu = menu_for(rest)
    if menu:
        return {"kind": "menu", "base": menu["base"]}
    rows = load_commands()
    takes_arg = {r[0]: bool(r[2]) for r in rows}
    names = sorted(takes_arg, key=lambda n: len(_base(n)), reverse=True)
    for name in names:
        b = _base(name).lower()
        tail = rest[len(b):].lstrip(" :").strip() if rest.startswith(b) else ""
        # "slash go to the store" must not become `/go to the store`: words after a
        # command are an argument only if that command takes input (or it is a bare
        # number, like `down 3`).
        if rest == b or (rest.startswith(b) and rest[len(b)] in " :"
                         and (takes_arg[name] or re.fullmatch(r"\d+", tail))):
            arg = tail
            # keep the operator's original casing/punctuation for the argument
            if arg:
                parts = re.split(re.escape(b.split()[-1]), m.group(1), maxsplit=1, flags=re.I)
                # Use the original casing only if it is the SAME argument once
                # cleaned — never re-introduce spoken words like "colon".
                if len(parts) > 1:
                    cand = parts[-1].lstrip(" :").strip(" .")
                    if cand and _normalise(cand) == arg:
                        arg = cand
            send = "/" + _base(name) + (":" if name.endswith(":") else "") + (f" {arg}" if arg else "")
            return {"kind": "run", "name": name, "base": _base(name), "arg": arg,
                    "send": send, "gated": is_gated(name)}
    bases = [_base(n) for n in names]
    if len(rest.split()) > 3 or "/" in m.group(1):
        return None   # a sentence ("slash the budget…") or a dictated path ("/etc/hosts"): not a command
    suggest = difflib.get_close_matches(rest, bases, n=4, cutoff=0.6)
    if not suggest:
        return None   # "slash prices now": nothing resembles a command, so let the model answer
    return {"kind": "unknown", "said": rest, "suggest": suggest}


_SECTION_ORDER = ["general", "settings", "tools", "plan", "nav", "exit"]
_SECTION_TITLE = {"general": "General", "settings": "Settings & modes", "tools": "Tools",
                  "plan": "Plan mode", "nav": "Scrolling", "exit": "Exit"}
GUIDE_PAGE_LINES = 7


def _fit(text: str, width: int) -> str:
    """Cut at a word boundary with an ellipsis — never mid-word."""
    if len(text) <= width:
        return text
    cut = text[: width - 1].rsplit(" ", 1)[0]
    return (cut if len(cut) > width // 2 else text[: width - 1]) + "…"


def guide_pages() -> list[str]:
    """Card pages: how to use it, then every command grouped by section with the
    exact words to say. Built from the registry, so it can't go stale."""
    pages = ["HOW TO USE SENSEI BY VOICE\n"
             "Hold the trigger and say\n"
             "\"slash\" + a command name:\n\n"
             " slash compact  - runs it\n"
             " slash mode     - opens a menu\n"
             " slash plan     - plan mode\n"
             " slash models   - pick a model\n"
             " slash sensei help - its own help\n"
             "\"slash help\" = this guide"]
    rows = load_commands()
    by_sec: dict[str, list[tuple]] = {}
    for r in rows:
        by_sec.setdefault(r[3], []).append(r)
    secs = [x for x in _SECTION_ORDER if x in by_sec] + [x for x in by_sec if x not in _SECTION_ORDER]
    for sec in secs:
        items = sorted(by_sec[sec], key=lambda r: r[0].lower())
        chunks = [items[i:i + GUIDE_PAGE_LINES] for i in range(0, len(items), GUIDE_PAGE_LINES)]
        for ci, chunk in enumerate(chunks, 1):
            head = f"{_SECTION_TITLE.get(sec, sec.title())}  ({ci}/{len(chunks)})"
            lines = []
            for name, hint, needs_arg, _s in chunk:
                words = _base(name).replace("_", " ")
                say = f"slash {words}" + (" …" if needs_arg else "")
                lines.append(_fit(f"{say} - {hint}", 33))
            pages.append(head + "\n\n" + "\n".join(lines))
    return pages


def list_text() -> str:
    """The full alphabetical command list, for the thread window."""
    rows = sorted(load_commands(), key=lambda r: r[0].lower())
    out = [f"SENSEI SLASH COMMANDS ({len(rows)}) — say \"slash <name>\" to run one", ""]
    for name, hint, needs_arg, section in rows:
        out.append(f"/{name:<24} {hint}  [{section}]{' (needs input)' if needs_arg else ''}")
    return "\n".join(out)


def list_summary() -> str:
    rows = load_commands()
    sections: dict[str, int] = {}
    for r in rows:
        sections[r[3]] = sections.get(r[3], 0) + 1
    parts = ", ".join(f"{n} {s}" for s, n in sorted(sections.items(), key=lambda kv: -kv[1]))
    return (f"You have {len(rows)} Sensei slash commands: {parts}. "
            "The full alphabetical list is in your Tahuti window. "
            "Say slash and a command name to run it.")


# --------------------------------------------------------------------------
# one driver at a time
# --------------------------------------------------------------------------
import contextlib  # noqa: E402
import fcntl  # noqa: E402

_LOCK_PATH = Path.home() / ".tahuti" / "sensei_slash.lock"


@contextlib.contextmanager
def sensei_lock(timeout: float = 240.0):
    """Only one goal may type into the Sensei pane at a time.

    Found 2026-11-03: the runner handles goals concurrently, and two spoken
    slash commands landing in the same second were both typing into one TUI
    (garbled input, mixed-up output). flock works across threads AND processes
    (the model picker presenter is its own process). Waits, then proceeds
    anyway rather than deadlocking a goal forever.
    """
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(_LOCK_PATH), os.O_CREAT | os.O_RDWR, 0o600)
    got = False
    try:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                got = True
                break
            except OSError:
                time.sleep(0.25)
        yield got
    finally:
        if got:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


# --------------------------------------------------------------------------
# tmux I/O
# --------------------------------------------------------------------------
def _tmux(*args: str, timeout: float = 5.0) -> subprocess.CompletedProcess:
    return subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=timeout)


def session_name() -> str:
    name = os.environ.get("SENSEI_SESSION_NAME", "").strip()
    if name:
        return name
    try:
        name = (Path.home() / ".master_ai_session_name").read_text(encoding="utf-8").strip()
        if name:
            return name
    except Exception:
        pass
    return "master-ai"


def _ppid(pid: int) -> int:
    try:
        return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
    except Exception:
        return 0


def _descends_from(pid: int, ancestor: int) -> bool:
    for _ in range(12):
        if pid == ancestor:
            return True
        pid = _ppid(pid)
        if pid <= 1:
            return False
    return False


def sensei_pane(session: str) -> Optional[str]:
    """tmux pane id whose process tree really contains master_ai.py, else None.
    (Not pane_current_command: that says "bash" under the supervisor loop.)"""
    if _tmux("has-session", "-t", session).returncode != 0:
        return None
    pids = subprocess.run(["pgrep", "-f", r"python3? .*master_ai\.py"],
                          capture_output=True, text=True).stdout.split()
    if not pids:
        return None
    panes = _tmux("list-panes", "-s", "-t", session, "-F", "#{pane_id} #{pane_pid}").stdout.splitlines()
    for line in panes:
        try:
            pane_id, pane_pid = line.split()
            if any(_descends_from(int(p), int(pane_pid)) for p in pids):
                return pane_id
        except ValueError:
            continue
    return None


def _capture(pane: str) -> str:
    return _tmux("capture-pane", "-p", "-t", pane, "-S", "-400").stdout


def capture_visible(pane: str) -> str:
    """Only what is on screen right now — no scrollback. Use this to READ a
    command's output; scrollback holds old shell text from before Sensei began."""
    return _tmux("capture-pane", "-p", "-t", pane).stdout


_CLOCK_RE = re.compile(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2} [AP]M")


def _wait_settled(pane: str, settle: float, timeout: float, min_wait: float = 0.0,
                  stop=None) -> str:
    """Poll until the screen stops changing for `settle` seconds.

    `stop(screen)` -> True ends the wait early (e.g. a menu has appeared); one
    more short pause lets the redraw finish. Sensei can sit unchanged for ~2s
    before a picker draws, so plain "unchanged" is not proof it has answered."""
    start = time.time()
    last, last_change = _capture(pane), time.time()
    while time.time() - start < timeout:
        time.sleep(0.4)
        cur = _capture(pane)
        # Sensei's banner carries a live clock; it must not count as "changed".
        if _CLOCK_RE.sub("", cur) != _CLOCK_RE.sub("", last):
            last, last_change = cur, time.time()
        if stop is not None and stop(cur):
            time.sleep(0.8)
            return _capture(pane)
        if time.time() - last_change >= settle and time.time() - start >= min_wait:
            break
    return last


def _supervisor_line() -> str:
    try:
        m = re.search(r"^SUPERVISOR='([^']*)'", AUTOSTART.read_text(encoding="utf-8"), re.M)
        if m:
            return m.group(1)
    except Exception:
        pass
    return "cd ~ && python3 ~/scripts/master_ai.py"


def _sensei_process_exists() -> bool:
    return bool(subprocess.run(["pgrep", "-f", r"python3? .*master_ai\.py"],
                               capture_output=True, text=True).stdout.split())


def ensure_sensei(timeout: float = 60.0) -> tuple[Optional[str], bool]:
    """(pane_id or None, started_it). Starts Sensei in window 0 if it is down.

    Deliberately targets window 0 explicitly: tmux's bare `-t session` means
    the ACTIVE window, which is Tahuti's thread window, not Sensei's.

    One failed process lookup is NOT proof Sensei is down. Ctrl-C into a live
    Sensei kills its in-flight work, so the restart keys are sent only when
    repeated lookups agree, no master_ai.py process exists at all, and window 0
    shows no Sensei screen (review finding, 2026-11-04).
    """
    sess = session_name()
    for attempt in range(3):
        pane = sensei_pane(sess)
        if pane:
            return pane, False
        if attempt < 2:
            time.sleep(1.0)
    if _tmux("has-session", "-t", sess).returncode != 0:
        subprocess.run(["bash", str(AUTOSTART)], capture_output=True, timeout=30)
    else:
        target = f"{sess}:0"
        if _sensei_process_exists() or is_ready(target):
            return None, False   # it is there; I just can't match its pane. Touch nothing.
        _tmux("send-keys", "-t", target, "C-c")
        time.sleep(0.3)
        _tmux("send-keys", "-t", target, "C-u")
        time.sleep(0.3)
        _tmux("send-keys", "-t", target, "-l", "--", _supervisor_line())
        _tmux("send-keys", "-t", target, "Enter")
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1.0)
        pane = sensei_pane(sess)
        if pane:
            # Process exists != prompt ready: wait for the real footer.
            wait_ready(pane, timeout=max(5.0, deadline - time.time()))
            return pane, True
    return None, True


_BOX = "│┃║╔╗╚╝╠╣═─━┌┐└┘├┤╭╮╰╯"
# Sensei's idle footer, e.g. "MODE:AUTO · MODEL:kimi-k2.7-code · CTX:0%" and
# the hint row "MODE AUTO · /help · /model". Present only when the TUI is up
# and waiting for input.
_READY_RE = re.compile(r"MODE[: ]\s*\w+.*(?:CTX:|/help)", re.I)


def _clean_line(ln: str) -> str:
    return re.sub(r"\s+", " ", ln.strip(_BOX + " ")).strip()


def is_ready(pane: str) -> bool:
    # VISIBLE screen only: scrollback keeps an old footer from a previous Sensei
    # run, which made a still-starting Sensei look ready (2026-11-04).
    return bool(_READY_RE.search(capture_visible(pane)))


# Sensei has modal screens that LOOK ready (the footer is still drawn) but treat
# whatever you type next as input to that screen — or, worse, as chat for the
# model, which then acts on it. Found 2026-11-03: after `/hub`, the next
# commands were swallowed as chat and Sensei's agent ran real shell commands.
_PAGER_RE = re.compile(r"\bq\s*=\s*(?:close|quit)\b|\bn\s*=\s*next\b", re.I)
_HUB_PROMPT_RE = re.compile(r"^\s*(?:🥷\s*)?(?:hub|help|tour|tutorial)\s*>\s*$", re.I | re.M)
_PICKER_RE = re.compile(r"┌[^\n]*\|\s*[^|\n]+?\s*\|[^\n]*┐")


def _live_body(screen: str) -> list[str]:
    """Cleaned non-empty chat lines directly above Sensei's input box — the LIVE
    area. Earlier lines are transcript history (a closed /hub's footer stays
    there forever), so they must never decide whether a menu is open."""
    plain = [re.sub(r"\x1b\[[0-9;]*m", "", l) for l in screen.splitlines()]
    end = input_box_top(plain)
    body = [_clean_line(l) for l in plain[:end]]
    return [l for l in body if l and l != "🥷" and set(l) - set("─━═╔╗╚╝╠╣║│┌┐└┘ ")]


# A yes/no or numbered question Sensei is waiting on (permission walkthrough,
# confirm gates, "Continue anyway? (y/N)"). Typing ANYTHING answers it, so these
# must be detected and never typed into. Found 2026-11-04: a probe sent command
# text into `/perms` and it answered "No" to four permissions.
_PROMPT_RE = re.compile(
    r"Permission \d+ of \d+|\((?:y/n|y/N|Y/n)\)|\[(?:y/n|y/N|Y/n)\]|Choose \([\d/]+\)|"
    r"Yes to All|\b1\) Yes\b", re.I)


def modal_state(screen: str) -> Optional[str]:
    """None if Sensei is at its idle prompt, else "prompt" | "pager" | "hub" | "picker".

    Only the last few lines above the input box count; see _live_body."""
    body = _live_body(screen)
    tail = body[-4:]
    if any(_PROMPT_RE.search(l) for l in body[-10:]):
        return "prompt"
    if any(_PAGER_RE.search(l) for l in tail):
        return "pager"
    if any(_HUB_PROMPT_RE.search(l) for l in body[-2:]):
        return "hub"
    # A picker is an open box whose rows are radio bullets, sitting right above
    # the input box (nothing printed after it).
    plain = [re.sub(r"\x1b\[[0-9;]*m", "", l) for l in screen.splitlines()]
    end = input_box_top(plain)
    region = plain[:end]
    box_end = -1
    for i in range(len(region) - 1, -1, -1):
        if "└" in region[i]:
            box_end = i
            break
    if box_end >= 0 and not [l for l in region[box_end + 1:] if l.strip()]:
        top = -1
        for i in range(box_end, -1, -1):
            if "┌" in region[i]:
                top = i
                break
        if top >= 0 and any(re.match(r"^\s*[│|]\s*\([ *]\)\s", l) for l in region[top:box_end]):
            return "picker"
    return None


def ensure_idle(pane: str, tries: int = 6, patience_s: float = 40.0) -> bool:
    """Leave any modal screen so the NEXT thing typed is a real command.
    True only once Sensei shows its idle prompt.

    Keys are sent ONLY to close a menu I can positively see (a picker or pager).
    If Sensei is simply not ready — still starting, redrawing, answering — I wait:
    typing `q` + Enter at that moment reaches the model as chat and its agent acts
    on it (review finding, 2026-11-04)."""
    deadline = time.time() + patience_s
    closes = 0
    while time.time() < deadline:
        state = modal_state(_capture(pane))
        if state is None and is_ready(pane):
            return True
        if state == "prompt":
            return False   # a question is waiting: any key would answer it
        if state is None:
            time.sleep(1.5)   # not ready, no menu to close: wait, never type
            continue
        if closes >= tries:
            break
        closes += 1
        if state == "picker":
            _tmux("send-keys", "-t", pane, "Escape")
        else:
            # Pagers/hub read a LINE: clear any half-typed text, then `q` + Enter.
            # (A bare `q` just sits in the input bar and never leaves the pager.)
            _tmux("send-keys", "-t", pane, "C-u")
            time.sleep(0.2)
            _tmux("send-keys", "-t", pane, "-l", "--", "q")
            _tmux("send-keys", "-t", pane, "Enter")
        time.sleep(1.5)
    return modal_state(_capture(pane)) is None and is_ready(pane)


def wait_ready(pane: str, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_ready(pane):
            return True
        time.sleep(1.0)
    return False


def _extract_new_output(before: str, after: str) -> str:
    """What the command added to the screen. Sensei is a full-screen TUI that
    redraws (it does not scroll), so diff screen against screen."""
    seen = {_clean_line(l) for l in before.splitlines()}
    after_clean = [_clean_line(l) for l in after.splitlines()]
    after_clean = [l for l in after_clean if l]
    new = [l for l in after_clean if l not in seen]
    if not new:  # identical output to last time (e.g. /doctor twice): show the screen
        new = after_clean[-25:]
    return "\n".join(new).strip()


_INPUT_TITLE_RE = re.compile(r"┌[─\s]*\|[^|\n]*\d{6,}[^|\n]*\|")   # the input box: "┌──|  … 1793745641 ○ |──┐"
_FOOTER_LINE_RE = re.compile(r"MODE:\S+\s+·\s+MODEL:")


def input_box_top(plain: list[str]) -> int:
    """Row of the input box's top border. Found by the `MODE:… MODEL:…` footer
    inside it, NOT by its title — the title changes (a long number, then
    "✏ chat-name · 1" once a chat is named) and a title match silently stops
    working. Falls back to the old title match, then to the screen's end."""
    for i in range(len(plain) - 1, -1, -1):
        if _FOOTER_LINE_RE.search(plain[i]):
            for j in range(i - 1, -1, -1):
                if "┌" in plain[j]:
                    return j
            break
    for i in range(len(plain) - 1, -1, -1):
        if _INPUT_TITLE_RE.search(plain[i]):
            return i
    return len(plain)


_HEADER_RE = re.compile(r"MASTER AI\s*[—–-]\s*SENSEI\s+\d")          # "🥷 MASTER AI — SENSEI 11/03/2026 …"


def chat_area(screen: str) -> list[str]:
    """Cleaned, non-empty lines of Sensei's chat pane (everything above the input box)."""
    plain = [re.sub(r"\x1b\[[0-9;]*m", "", l) for l in screen.splitlines()]
    end = input_box_top(plain)
    out = [_clean_line(l) for l in plain[:end]]
    return [l for l in out if l and set(l) - set("─━═╔╗╚╝╠╣║│ ")]


def extract_output(before: str, after: str, send: str) -> str:
    """What `send` printed. Reads the chat pane after the command's echo
    ("> doctor") instead of diffing screens: the TUI redraws, so a repeat of an
    earlier command looks 'unchanged' to a diff and its output was lost."""
    area = chat_area(after)
    key = send.lstrip("/").strip().lower()
    idx = -1
    for i in range(len(area) - 1, -1, -1):
        if area[i].startswith(">") and area[i].lstrip("> ").strip().lower().startswith(key):
            idx = i
            break
    if idx >= 0:
        new = area[idx + 1:]
    else:
        seen = set(chat_area(before))
        new = [l for l in area if l not in seen]
    new = [l for l in new if not _HEADER_RE.search(l)]
    if len([l for l in new if len(l) > 3]) < 2:  # echo scrolled off / identical redraw
        new = [l for l in area if not _HEADER_RE.search(l)][-30:]
    return "\n".join(new).strip()


def run_in_sensei(pane: str, send: str, timeout: float = 25.0) -> str:
    """Type `send` into the live Sensei pane once it is ready, wait for the
    redraw to settle, return what appeared."""
    if not wait_ready(pane, timeout=30):
        raise RuntimeError("Sensei isn't ready yet")
    if not ensure_idle(pane):
        # Never type into a screen we can't leave: it would reach the model as chat.
        raise RuntimeError("Sensei is in a menu I couldn't close")
    before = capture_visible(pane)
    _tmux("send-keys", "-t", pane, "-l", "--", send)
    _tmux("send-keys", "-t", pane, "Enter")
    _wait_settled(pane, settle=5.0, timeout=timeout, min_wait=5.0)
    out = extract_output(before, capture_visible(pane), send)
    ensure_idle(pane)  # leave /hub, /help, pickers behind for the next caller
    return out


_FIXED_SPEECH = {
    "compact": "Compacted. I saved and summarized the conversation, and Sensei restarted with the shorter history.",
    "compress": "Compacted. I saved and summarized the conversation, and Sensei restarted with the shorter history.",
}


def spoken_digest(output: str, base: str, max_chars: int = 320) -> str:
    """A short, speakable summary — the full output goes to the thread window."""
    # Commands whose screen output is NOT the result (compact restarts Sensei and
    # leaves its welcome banner): say what happened instead of reading the banner.
    fixed = _FIXED_SPEECH.get(base.strip().lstrip("/").lower())
    if fixed:
        return fixed
    lines = [re.sub(r"\x1b\[[0-9;]*m", "", ln).strip() for ln in output.splitlines()]
    lines = [ln for ln in lines if ln and not set(ln) <= set("-=_│┃─━ ╭╮╰╯")]
    if not lines:
        return f"Ran slash {base} in Sensei; it printed nothing."
    head = " ".join(lines[:4])
    if len(head) > max_chars:
        head = head[:max_chars].rsplit(" ", 1)[0] + " …"
    more = f" {len(lines)} lines in all." if len(lines) > 4 else ""
    return f"Ran slash {base}. {head}{more} Full output is in your Tahuti window."
