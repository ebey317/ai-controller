"""Deterministic slash-parse accuracy bench (v0) — C1 and C2.

C1: every command in Sensei's registry parsed with three phrasings each —
    plain ("slash x"), comma-spoken ("slash, x"), and the dictated-colon
    form for colon-suffixed names ("agent" vs "agent:"). Expected: the
    SAME registry command is selected; accuracy >= 99%.
C2: negatives (sentences starting with "slash", dictated paths, ordinary
    requests like "show me the docker commands") must NOT be intercepted;
    false-intercept rate <= 1%.

Pure Python, no audio, no model, no network — safe to gate every commit.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import sensei_slash as sb  # noqa: E402

ROWS = sb.load_commands()
assert len(ROWS) >= 100, f"registry drifted: {len(ROWS)} commands (expected ~121)"


def _phrasings(name: str) -> list[str]:
    """Three spoken forms each dictation could produce for one command."""
    spoken = name.replace(":", " colon") if name.endswith(":") else name
    base = spoken
    return [f"slash {base}", f"slash, {base}", f"Slash {base.title()}"]


def _expected_kind(name: str) -> str:
    """The kind each registry row routes to (special rows have their own
    lanes: help/commands open the guide list, `model` is a picker, alias
    rows like `plan` are plain runs of their target)."""
    base = name.rstrip(":").lower()
    if base in sb._GUIDE_PHRASES or base == "commands":
        return "list"
    if base == "model":
        return "picker"
    if base in sb._ALIASES:
        return "run"
    if base not in sb._NO_MENU and sb.menu_for(base):
        return "menu"
    return "run"


def _routes_to_expected(filtered: dict, name: str) -> bool:
    base = name.rstrip(":").lower()
    if filtered["kind"] != _expected_kind(name):
        return False
    if filtered["kind"] == "run":
        want_base = sb._ALIASES[base] if base in sb._ALIASES else base
        got_base = str(filtered.get("base", "")).lower()
        # alias rows are runs of THEIR TARGET ("plan" -> "mode plan")
        if got_base != want_base:
            return False
        if name.endswith(":"):
            send = str(filtered.get("send", ""))
            if not send.startswith("/" + want_base):
                return False
    return True


def test_c1_every_command_routes_exactly_across_phrasings():
    failures = []
    attempts = 0
    for name, _hint, _takes_arg, _section in ROWS:
        for phrasing in _phrasings(name):
            attempts += 1
            got = sb.parse(phrasing)
            if got is None:
                failures.append(
                    (phrasing, name, "NOT INTERCEPTED (fell through to the model)")
                )
                continue
            if not _routes_to_expected(got, name):
                failures.append((phrasing, name, f"-> {got}"))
    accuracy = 1.0 - (len(failures) / attempts)
    assert accuracy >= 0.99, (
        f"C1 slash-parse accuracy {accuracy:.1%} < 99% ({len(failures)}/{attempts} wrong)\n"
        + "\n".join(
            f"  say {t!r} -> want {name!r}, {why}" for t, name, why in failures[:25]
        )
    )


def test_c1_special_phrases_still_route():
    for said, kind in [
        ("slash help", "list"),
        ("slash commands guide", "list"),
        ("slash how do i use this", "list"),
        ("slash reminders on", "reminders"),
        ("slash readme", "readme"),
        ("slash model", "picker"),
    ]:
        got = sb.parse(said)
        assert got is not None and got["kind"] == kind, f"{said!r} -> {got}"
    # alias special phrases are runs of their TARGET, not menus
    for said, target in [("slash plan", "mode plan"), ("slash review", "mode review")]:
        got = sb.parse(said)
        assert (
            got is not None and got["kind"] == "run" and got["send"] == f"/{target}"
        ), f"{said!r} -> {got}"


def test_c2_negatives_arent_intercepted():
    # False-intercept budget: <= 1%. These are REAL utterances that must go to
    # the model (or nowhere); intercepting ANY of them hijacks ordinary speech.
    negatives = [
        "slash the budget by ten percent",
        "slash and burn that draft",
        "slash the budget for today",
        "slash prices now",
        "slash /etc/hosts",
        "/etc/hosts",
        "slash /tmp/cache and report",
        "slash my friend's book list",
        "slash what time it is",
        "show me the docker commands",
        "give me the git commands",
        "tell me the keyboard commands",
        "what are the slash commands for git",
        "slash anything you know",
        "slash the whole plan down",
        "slash it out with them",
        "slash through the budget line by line",
        "slash prices are up",
        "slash the tires and rotate",
        "slash my budget by half",
    ]
    false_intercepts = [(n, sb.parse(n)) for n in negatives if sb.parse(n) is not None]
    rate = len(false_intercepts) / len(negatives)
    assert rate <= 0.01, f"C2 false-intercept rate {rate:.1%} > 1%: " + ", ".join(
        f"{n!r} -> {got}" for n, got in false_intercepts
    )


def test_c2_clearly_list_requests_still_open_the_list():
    for said in ["list slash commands", "show commands", "slash commands", "slash"]:
        got = sb.parse(said)
        assert got is not None and got["kind"] == "list", f"{said!r} -> {got}"
