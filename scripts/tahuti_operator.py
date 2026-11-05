"""tahuti_operator.py — what Tahuti already KNOWS about the operator.

2026-11-05 failures this fixes: "what's the weather" -> "tell me your city"
(he is in Indianapolis and has said so many times), and "open my projects
folder (on my desktop)" -> opened the file manager and told him to click.
The facts live in an editable file, not in a prompt string:

    ~/.tahuti/operator_profile.md    (seeded on first use; edit freely)

    from tahuti_operator import profile_text, resolve_folder, weather_line
"""
from __future__ import annotations

import re
from pathlib import Path

HOME = Path.home()
PROFILE = HOME / ".tahuti" / "operator_profile.md"

_SEED = """\
# What Tahuti knows about the operator (edit me)
- Name: Lijah (Elijah). Lives in Indianapolis, Indiana. Time zone: America/Indiana/Indianapolis (Eastern).
- Weather/local questions: use Indianapolis, Indiana unless told otherwise. NEVER ask for the city.
- Projects folders: ~/Desktop/Projects (the one "on my desktop") and ~/projects.
- Shared notes folder: ~/MD. Main code: ~/ai-controller, ~/master-ai-cli.
- He speaks by voice: short, plain sentences. He has said all of this many times; do not ask again.
"""

# spoken word -> candidate folders, first existing wins
_ALIASES = {
    "projects": ["~/Desktop/Projects", "~/projects"],
    "my projects": ["~/Desktop/Projects", "~/projects"],
    "desktop": ["~/Desktop"],
    "documents": ["~/Documents"],
    "downloads": ["~/Downloads"],
    "home": ["~"],
    "notes": ["~/MD"],
    "md": ["~/MD"],
}


def profile_text() -> str:
    """The profile, seeding the file the first time. '' if unreadable."""
    try:
        if not PROFILE.exists():
            PROFILE.parent.mkdir(parents=True, exist_ok=True)
            PROFILE.write_text(_SEED, encoding="utf-8")
        return PROFILE.read_text(encoding="utf-8")[:2000].strip()
    except OSError:
        return _SEED.strip()


def profile_prompt() -> str:
    """One prompt clause telling the model what it already knows."""
    t = profile_text()
    return (" WHAT YOU ALREADY KNOW ABOUT THE OPERATOR (use it, never ask him "
            "for it again): " + re.sub(r"\s+", " ", t)) if t else ""


def city() -> str:
    m = re.search(r"Lives in ([^.\n]+)", profile_text())
    return (m.group(1).strip() if m else "Indianapolis, Indiana")


def _expand(a: str) -> Path:
    """~ means THIS module's HOME (testable), not whatever the process thinks."""
    a = a.strip()
    if a == "~":
        return HOME
    return HOME / a[2:] if a.startswith("~/") else Path(a)


def _candidates(name: str) -> list[Path]:
    n = re.sub(r"\s+", " ", (name or "").strip().lower())
    n = re.sub(r"^(my|the)\s+", "", n)
    n = re.sub(r"\s+(that'?s |which is |that is )?(on|in|from) (my |the )?desktop$", "", n).strip()
    n = re.sub(r"\s+(folder|directory|dir)$", "", n).strip()
    on_desktop = bool(re.search(r"\bdesktop\b", (name or "").lower())) and n != "desktop"
    out: list[Path] = []
    if name.strip().startswith(("~", "/")):
        out.append(_expand(name))
    for a in _ALIASES.get(n, []) or _ALIASES.get("my " + n, []):
        out.append(_expand(a))
    # a folder literally named that, on the Desktop or at home
    for base in ([HOME / "Desktop", HOME] if on_desktop else [HOME, HOME / "Desktop"]):
        try:
            for child in base.iterdir():
                if child.is_dir() and child.name.lower() == n:
                    out.append(child)
        except OSError:
            pass
    if on_desktop:  # "on my desktop" wins when more than one match
        out.sort(key=lambda p: 0 if str(p).startswith(str(HOME / "Desktop")) else 1)
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def resolve_folder(name: str, is_secret=lambda p: False) -> tuple[Path | None, str]:
    """(folder, note). Home directory only, never a secret location, must
    exist. note says which one was chosen when there was more than one."""
    cands = []
    for p in _candidates(name):
        try:
            rp = p.resolve()
            rp.relative_to(HOME.resolve())
        except (OSError, ValueError):
            continue
        if rp.is_dir() and not is_secret(rp):
            cands.append(rp)
    if not cands:
        return None, f"no folder called '{name}' under your home directory"
    note = f"also found {cands[1]}" if len(cands) > 1 else ""
    return cands[0], note


def weather_line(fetch=None) -> str | None:
    """One spoken sentence of current weather for the operator's city, or
    None when the lookup fails (the caller then says so — no guessing)."""
    where = city()
    url = "https://wttr.in/" + re.sub(r"[^A-Za-z0-9]+", "+", where).strip("+") + "?format=%C,+%t,+feels+like+%f,+wind+%w,+humidity+%h&u"
    try:
        if fetch is None:
            import httpx
            fetch = lambda u: httpx.get(u, timeout=6, headers={"User-Agent": "tahuti"}).text  # noqa: E731
        raw = (fetch(url) or "").strip()
    except Exception:
        return None
    if not raw or len(raw) > 200 or "<" in raw or "Unknown" in raw:
        return None
    return f"In {where} right now: {raw}."


_WEATHER_RE = re.compile(r"\b(weather|temperature|forecast|how (hot|cold|warm) is it|is it (raining|snowing))\b", re.I)


def is_weather_question(text: str) -> bool:
    return bool(_WEATHER_RE.search(text or ""))
