#!/usr/bin/env python3
"""whats_new.py — what the keyboard's "sensei Upd" pin shows.

2026-11-06: the pin used to run update_master_ai.sh, which never listed
anything and restarted Sensei. This only READS: it never pulls, installs or
restarts anything.

  1. Master AI: commits in ~/master-ai-cli you have not been shown yet.
  2. Claude Code, OpenCode, Hermes: your installed version vs the latest
     release, plus the release notes you have not seen.

    python3 whats_new.py            # print, remember what was shown
    python3 whats_new.py --peek     # print, remember nothing
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
STATE = HOME / ".tahuti" / "whatsnew_seen.json"
REPO = HOME / "master-ai-cli"
TOOLS = [
    # name, github repo, version command, tag pattern for real releases
    (
        "Claude Code",
        "anthropics/claude-code",
        ["claude", "--version"],
        r"^v\d+\.\d+\.\d+$",
    ),
    ("OpenCode", "sst/opencode", ["opencode", "--version"], r"^v\d+\.\d+\.\d+$"),
    ("Hermes", "NousResearch/hermes-agent", ["hermes", "--version"], r"^v0\.\d+\.\d+$"),
]
MAX_NOTES = 8  # lines of notes per release
MAX_COMMITS = 15


def _run(cmd: list[str], timeout: int = 15) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _vkey(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def installed_version(cmd: list[str]) -> str:
    m = re.search(r"v?(\d+\.\d+\.\d+)", _run(cmd))
    return m.group(1) if m else ""


def humanize(subject: str) -> str:
    s = subject.strip()
    m = re.match(
        r"^(fix|feat|refactor|perf|docs|chore|test|ci)(\([^)]*\))?:\s*(.+)$", s
    )
    if m:
        verb = {
            "fix": "Fixed",
            "feat": "Added",
            "refactor": "Cleaned up",
            "perf": "Sped up",
            "docs": "Notes",
            "chore": "Housekeeping",
            "test": "Tests",
            "ci": "Checks",
        }[m.group(1)]
        return f"{verb}: {m.group(3)[:1].upper()}{m.group(3)[1:]}"
    return s[:1].upper() + s[1:]


def load_seen() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def master_ai_section(seen: dict) -> tuple[list[str], str]:
    head = _run(["git", "-C", str(REPO), "rev-parse", "HEAD"])
    if not head:
        return ["  (could not read ~/master-ai-cli)"], ""
    last = seen.get("master_ai", "")
    base = ["git", "-C", str(REPO), "log", "--no-merges", "--pretty=format:%s"]
    if last and _run(["git", "-C", str(REPO), "cat-file", "-t", last]) == "commit":
        out = _run(base + [f"{last}..HEAD"])
    else:
        out = _run(base + ["--since=7 days ago"])
    subjects = [s for s in out.splitlines() if s.strip()]
    if not subjects:
        return ["  Nothing new since you last looked."], head
    lines = [f"  - {humanize(s)}" for s in subjects[:MAX_COMMITS]]
    if len(subjects) > MAX_COMMITS:
        lines.append(f"  ...and {len(subjects) - MAX_COMMITS} more")
    return lines, head


def releases(repo: str, pattern: str) -> list[dict]:
    raw = _run(["gh", "api", f"repos/{repo}/releases?per_page=15"], timeout=25)
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    ok = [
        r
        for r in data
        if re.match(pattern, r.get("tag_name", ""))
        and not r.get("prerelease")
        and not r.get("draft")
    ]
    return sorted(ok, key=lambda r: _vkey(r["tag_name"]), reverse=True)


def notes(body: str) -> list[str]:
    lines = [
        ln.strip()
        for ln in (body or "").splitlines()
        if ln.strip().startswith(("-", "*")) and len(ln.strip()) > 3
    ]
    return [f"      {ln[:170]}" for ln in lines[:MAX_NOTES]]


def tool_section(
    name: str, repo: str, vcmd: list[str], pattern: str, seen: dict
) -> tuple[list[str], str]:
    have = installed_version(vcmd)
    rels = releases(repo, pattern)
    if not rels:
        return [f"  {name}: could not reach GitHub (installed {have or '?'})"], ""
    latest = rels[0]["tag_name"].lstrip("v")
    if have and _vkey(have) < _vkey(latest):
        status = f"installed {have}, latest {latest}  -> UPDATE AVAILABLE"
    else:
        status = f"installed {have or '?'}, up to date"
    out = [f"  {name}: {status}"]
    floor = seen.get(name) or have or "0"
    new = [r for r in rels if _vkey(r["tag_name"]) > _vkey(floor)] or rels[:1]
    for r in new[:3]:
        out.append(f"    {r['tag_name']}  ({r.get('published_at', '')[:10]})")
        out.extend(notes(r.get("body", "")))
    return out, latest


def main(argv: list[str]) -> int:
    peek = "--peek" in argv
    seen = load_seen()
    print("WHAT'S NEW\n")
    print("Master AI / Sensei")
    lines, head = master_ai_section(seen)
    print("\n".join(lines))
    new_seen = dict(seen)
    if head:
        new_seen["master_ai"] = head
    for name, repo, vcmd, pat in TOOLS:
        print(f"\n{name}")
        lines, latest = tool_section(name, repo, vcmd, pat, seen)
        print("\n".join(lines))
        if latest:
            new_seen[name] = latest
    if not peek:
        try:
            STATE.parent.mkdir(parents=True, exist_ok=True)
            STATE.write_text(json.dumps(new_seen))
        except OSError:
            pass
    print("\nNothing was installed or restarted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
