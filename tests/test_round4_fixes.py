"""Round-4 review tests (2026-11-05) — the 6 items in round4_ai-controller.md.

Items 1-3 are T13/T14 REGRESSIONS and are tested through the REAL ptt path:
the release-critical functions are extracted from ptt_pynput's source (the
module is unimportable in a test process: file lock + listener threads) and
driven end-to-end. The clock ADVANCES on every time.time() call, so any
future code that compares a freshly-sampled time against a written stamp
(the exact round-3 regression) FAILS these tests — same-clock rigs pass
regressions accidentally. Item 5 drives the real `sensei_slash.parse`;
item 6 the real `mcp_tabs.to_url`.
"""

from __future__ import annotations

import ast
import json
import os
import sys
import types
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import avatar_pulse  # noqa: E402
import mcp_tabs  # noqa: E402
import sensei_slash as sb  # noqa: E402

PTT_SRC = SCRIPTS / "ptt_pynput.py"


class _QuietLog:
    warning = staticmethod(lambda *a, **k: None)  # type: ignore[attr-defined]
    info = warning
    debug = warning
    error = warning


@pytest.fixture()
def release_rig(tmp_path):
    """on_release + stop_and_send + caption IO against a temp caption file,
    an ADVANCING clock, a synchronised Thread stub and a recorder for the
    shared avatar-pulse killer."""
    capfile = tmp_path / "caption.json"
    clock = {"t": 300.0}

    def tick():  # every time.time() advances 7s: stamps are unique
        clock["t"] += 7.0
        return clock["t"]

    tree = ast.parse(PTT_SRC.read_text(encoding="utf-8"))
    want = {
        "on_release",
        "stop_and_send",
        "_take_still_current",
        "_should_clear_to_idle",
        "_write_caption",
        "_read_caption",
    }
    chunks = [
        ast.unparse(n)
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name in want
    ]
    found = {
        getattr(n, "name", "")
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name in want
    }
    assert not (want - found), f"extraction lost {want - found}"

    jobs: list[tuple] = []  # queued (target, args) by the Thread stub

    class _SyncThread:
        def __init__(self, target, args=(), daemon=None):
            self.target, self.args = target, args

        def start(self):
            jobs.append((self.target, self.args))

    killed: list[str] = []
    pulse_stub = types.SimpleNamespace(
        kill_avatar_pulse=lambda *a, **k: killed.append("default") or False
    )

    ns: dict = {
        "CAPTION_FILE": str(capfile),
        "log": _QuietLog(),
        "json": json,
        "os": os,
        "time": types.SimpleNamespace(time=tick),
        "threading": types.SimpleNamespace(Thread=_SyncThread),
        "keyboard": types.SimpleNamespace(
            Key=types.SimpleNamespace(f13=object(), ctrl_l=object(), ctrl_r=object())
        ),
        "avatar_pulse": pulse_stub,
        "_stop_and_send_impl": lambda *a, **k: None,  # replaced per-test
        "_ctrl_held": False,
        "_goal_take": False,
        "take_seq": 0,
    }
    exec(compile("\n".join(chunks), str(PTT_SRC), "exec"), ns)  # noqa: S102
    return ns, capfile, jobs, killed, clock


def _caption(capfile: Path) -> dict:
    return json.loads(capfile.read_text())


# ── Item 1 (REGRESSION, HIGH): idle-clear keys on the WRITER's stamp ─────────


def test_on_release_passes_the_writers_stamp(release_rig):
    """on_release must hand the stop thread the timestamp _write_caption put
    on disk — NOT a freshly sampled time.time() (round-4 #1: those always
    differed, so the guard never fired and the avatar stuck on 'sending')."""
    ns, capfile, jobs, _killed, _clock = release_rig
    ns["_goal_take"] = True
    ns["on_release"](ns["keyboard"].Key.f13)
    stamped = _caption(capfile)["time"]
    assert jobs, "no stop thread was spawned"
    _target, args = jobs[0]
    assert (
        args[1] == stamped
    ), f"thread got a separate time sample {args[1]}, not the writer's {stamped}"


def test_agent_take_own_sending_is_cleared_real_path(release_rig):
    """REAL end-to-end: agent take → 'sending' stamped → stop thread's
    finally clears to 'idle' (impl stub wrote nothing else)."""
    ns, capfile, jobs, _killed, _clock = release_rig
    ns["_goal_take"] = True
    ns["on_release"](ns["keyboard"].Key.f13)
    assert _caption(capfile)["state"] == "sending"
    t, a = jobs.pop(0)
    t(*a)  # run the stop thread inline
    assert _caption(capfile)["state"] == "idle"


def test_newer_takes_sending_survives_the_late_finally(release_rig):
    """REAL race, real stamps: take #1's finally runs AFTER take #2 wrote
    its own 'sending' stamp. Same-clock rigs can't tell these apart — the
    advancing clock makes every stamp unique, and the writer-stamp guard
    must leave take #2's caption exactly as it stands."""
    ns, capfile, jobs, _killed, _clock = release_rig
    key = ns["keyboard"].Key.f13
    ns["_goal_take"] = True
    ns["on_release"](key)  # take #1 stamps its 'sending'
    take1 = jobs.pop(0)
    ns["on_release"](key)  # take #2 stamps ITS 'sending'
    take2 = jobs.pop(0)
    live = capfile.read_text()  # take #2's live caption
    t, a = take1
    t(*a)  # take #1 runs LATE, take #2 live
    assert (
        capfile.read_text() == live
    ), "take #1's late finally clobbered take #2's live 'sending' caption"
    t, a = take2
    t(*a)  # take #2's own finally clears
    assert _caption(capfile)["state"] == "idle"


def test_dictation_take_never_touches_the_avatar_caption(release_rig):
    ns, capfile, jobs, _killed, _clock = release_rig
    ns["_goal_take"] = False
    ns["on_release"](ns["keyboard"].Key.f13)
    ns["_write_caption"]("", "sending")  # someone else's live stamp
    t, a = jobs.pop(0)
    t(*a)
    assert _caption(capfile)["state"] == "sending"


# ── Item 2: the take counter guards the global flag + captions ──────────────


def test_take_counter_bumps_per_agent_take_only(release_rig):
    """Round-5 #4: dictation takes must not consume/advance the counter — a
    later dictation may never orphan an agent take's cleanup. Agent takes
    still get distinct ids."""
    ns, _capfile, jobs, _killed, _clock = release_rig
    ns["on_release"](ns["keyboard"].Key.f13)  # dictation: no bump, id 0
    assert jobs[-1][1][0] is False and jobs[-1][1][2] == 0
    ns["_goal_take"] = True
    ns["on_release"](ns["keyboard"].Key.f13)  # agent take: id 1
    ns["_goal_take"] = True
    ns["on_release"](ns["keyboard"].Key.f13)  # agent take: id 2
    assert (
        jobs[-2][1][2] == 1 and jobs[-1][1][2] == 2
    ), "each AGENT release must open a distinct take id"


def test_late_first_take_cannot_reset_a_newer_take(release_rig):
    """Out-of-order threads: take #1's stop thread runs WHILE take #2 is
    still live. Everything take #1 does must be hands-off: its impl call
    sees it as stale and its finally leaves the caption alone."""
    ns, capfile, jobs, _killed, _clock = release_rig
    key = ns["keyboard"].Key.f13
    ns["_goal_take"] = True
    ns["on_release"](key)  # take #1 (stamp T1)
    take1 = jobs.pop(0)
    ns["on_release"](key)  # take #2 (stamp T2 > T1)
    take2 = jobs.pop(0)
    order: list[bool] = []
    ns["_stop_and_send_impl"] = lambda was, take: order.append(take == ns["take_seq"])
    t, a = take1
    t(*a)  # take #1 LATE, take #2 live
    assert order == [False], "the late impl call must see take #1 as stale"
    live = capfile.read_text()  # take #2's live 'sending'
    assert (
        json.loads(live)["state"] == "sending"
    ), "take #1's late finally wiped take #2's caption"
    t, a = take2
    t(*a)  # take #2 still current
    assert order == [
        False,
        True,
    ], "the impl must see take #1 as stale and take #2 as current"
    assert (
        json.loads(capfile.read_text())["state"] == "idle"
    ), "take #2's own finally never cleared its own stamp"


def test_impl_flag_resets_are_take_guarded():
    """Every `_goal_take = False` inside the real impl sits behind the
    `_take_still_current(take_id)` guard (round-4 #2)."""
    src = PTT_SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)
    impl = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_stop_and_send_impl"
    )
    resets = [
        n
        for n in ast.walk(impl)
        if isinstance(n, ast.Assign)
        and getattr(n.targets[0], "id", "") == "_goal_take"
        and getattr(n.value, "value", None) is False
    ]
    assert resets, "impl's flag resets disappeared"
    parents: dict = {}
    for p in ast.walk(impl):
        for attr in ("body", "orelse", "finalbody"):
            container = getattr(p, attr, None)
            if not isinstance(container, list):
                continue
            for child in container:
                parents[child] = p
    for node in resets:
        cur, guarded = parents.get(node), False
        while cur is not None:
            if isinstance(cur, ast.If) and "_take_still_current" in ast.unparse(
                cur.test
            ):
                guarded = True
                break
            cur = parents.get(cur)
        assert guarded, f"unguarded _goal_take reset at line {node.lineno}"


# ── Item 3: the release path kills the pulse through the SHARED helper ──────


def test_dictation_release_uses_shared_verified_pulse_kill(release_rig):
    ns, _capfile, jobs, killed, _clock = release_rig
    ns["on_release"](ns["keyboard"].Key.f13)  # plain dictation take
    assert killed == ["default"], "dictation release did not use avatar_pulse"
    ns["_goal_take"] = True
    ns["on_release"](ns["keyboard"].Key.f13)  # agent take
    assert killed == ["default"], "an agent take must NOT kill the pulse"


def test_avatar_pulse_shared_module_verifies_before_killing(monkeypatch, tmp_path):
    """The shared rule: uid match + avatar_approval in the cmdline."""
    pidfile = tmp_path / "pulse.pid"
    pidfile.write_text("4242")
    kills = []
    monkeypatch.setattr(
        avatar_pulse,
        "pulse_cmdline_uid",
        lambda pid: (b"python3 ninja/avatar_approval.py --pulse 180", os.getuid()),
    )
    monkeypatch.setattr(
        avatar_pulse.os, "kill", lambda pid, sig: kills.append((pid, sig))
    )
    assert avatar_pulse.kill_avatar_pulse(pidfile=str(pidfile)) is True
    assert kills == [(4242, 9)]

    # innocent process: same uid, wrong binary → refused, no signal shifted
    monkeypatch.setattr(
        avatar_pulse, "pulse_cmdline_uid", lambda pid: (b"bash", os.getuid())
    )
    assert avatar_pulse.kill_avatar_pulse(pidfile=str(pidfile)) is False
    # foreign uid → refused
    monkeypatch.setattr(
        avatar_pulse,
        "pulse_cmdline_uid",
        lambda pid: (b"python3 /p/avatar_approval.py", os.getuid() + 7),
    )
    assert avatar_pulse.kill_avatar_pulse(pidfile=str(pidfile)) is False
    assert kills == [(4242, 9)], "an unverified signal went out"


def test_avatar_pulse_dead_or_junk_pidfiles(monkeypatch, tmp_path):
    assert (
        avatar_pulse.kill_avatar_pulse(pidfile=str(tmp_path / "missing.pid")) is False
    )
    junk = tmp_path / "junk.pid"
    junk.write_text("not a pid\n")
    assert avatar_pulse.kill_avatar_pulse(pidfile=str(junk)) is False
    junk.write_text("1")  # pid <= 1: never killable
    assert avatar_pulse.kill_avatar_pulse(pidfile=str(junk)) is False
    junk.write_text("999999999999")
    assert avatar_pulse.kill_avatar_pulse(pidfile=str(junk)) is False  # vanished


def test_goal_runner_delegates_pulse_kill_to_avatar_pulse(monkeypatch, tmp_path):
    import tahuti_goal_runner as runner

    calls = []
    monkeypatch.setattr(
        avatar_pulse, "kill_avatar_pulse", lambda pidfile: calls.append(pidfile) or True
    )
    assert runner._kill_avatar_pulse(pidfile=str(tmp_path / "x.pid")) is True
    assert calls == [str(tmp_path / "x.pid")]


def test_all_pulse_kill_sites_use_the_checked_helper():
    """No bare os.kill(<pid>, 9) off the pid file in EITHER importer."""
    for script in ("tahuti_goal_runner.py", "ptt_pynput.py"):
        src = (SCRIPTS / script).read_text(encoding="utf-8")
        lines = [ln for ln in src.splitlines() if "tahuti-pulse.pid" in ln]
        bare = [
            ln
            for ln in lines
            if ("os.kill" in ln or "read_text" in ln)
            and "kill_avatar_pulse" not in ln
            and "pidfile=" not in ln
            and "world-writable" not in ln
        ]
        assert not bare, f"{script}: bare pid-file kill returned: {bare}"
        assert "import avatar_pulse" in src, f"{script} missing the shared import"


# ── Item 4: installer daemon-reload must not abort headless installs ────────


def test_install_daemon_reload_is_guarded():
    install = (SCRIPTS / "..").joinpath("install.sh").resolve()
    src = install.read_text(encoding="utf-8")
    assert "systemctl --user daemon-reload" in src
    assert "daemon-reload 2>/dev/null ||" in src, "daemon-reload became unguarded again"
    assert "WARNING: systemctl daemon-reload failed" in src
    import subprocess

    r = subprocess.run(["bash", "-n", str(install)], capture_output=True, text=True)
    assert r.returncode == 0, f"install.sh broken: {r.stderr}"


# ── Item 5: exact command words inside sentences are NOT commands ───────────


@pytest.mark.parametrize(
    "said",
    [
        "slash review the quarterly numbers",
        "slash status of the budget",
        "slash new hires from the list",
    ],
)
def test_command_words_inside_sentences_fall_through(said):
    assert sb.parse(said) is None, f"{said!r} was hijacked by the command lane"


@pytest.mark.parametrize(
    "said,kind",
    [
        ("slash git comit message", "unknown"),  # residual near-miss keeps it loud
        ("slash git comit", "unknown"),  # 2 words: still a command attempt
        ("slash go to the store", None),  # revised by round-4: sentence now
    ],
)
def test_round4_first_word_dispositions(said, kind):
    got = sb.parse(said)
    if kind is None:
        assert got is None, f"{said!r}: expected None, got {got}"
    else:
        assert got is not None and got["kind"] == kind
        if kind == "unknown":
            assert got.get("suggest"), f"{said!r}: unknown must carry suggestions"


@pytest.mark.parametrize(
    "said,expect_run",
    [
        ("slash new", True),
        ("slash git", True),
        ("slash down 3", True),
        ("slash remember: I like tea", True),
        ("slash /compact", True),
        ("slash prices now", False),
        ("slash the budget for today", False),
        ("slash /etc/hosts", False),
        ("slash it out with them", False),
        ("slash the budget by ten percent", False),
        ("slash and burn that draft", False),
    ],
)
def test_round4_keeps_every_other_disposition(said, expect_run):
    """Regression net: the round-4 rework changed NOTHING else."""
    got = sb.parse(said)
    if expect_run:
        assert got and got["kind"] in ("run", "menu"), f"{said!r} -> {got}"
    else:
        assert got is None, f"{said!r} -> {got}"


# ── Item 6: to_url treats colon-paths as PATHS, not URIs ────────────────────


def test_to_url_colon_paths_are_paths_not_uris():
    got = mcp_tabs.to_url("localhost:8080/index.html")
    assert got.startswith("file://") and not got.startswith("localhost:")
    got = mcp_tabs.to_url("notes:2026.md")
    assert got.startswith("file://") and not got.startswith("notes:")


@pytest.mark.parametrize(
    "uri",
    [
        "https://example.com/x",
        "file:///tmp/a.html",
        "mailto:you@example.com",
        "tel:911",
        "data:text/plain,hi",
        "magnet:?xt=urn:btih:x",
        "about:config",
        "chrome://flags",
        "ftp://host/file.txt",
    ],
)
def test_to_url_known_schemes_still_pass_through(uri):
    assert mcp_tabs.to_url(uri) == uri


def test_to_url_unknown_colon_prefix_is_a_path(tmp_path):
    got = mcp_tabs.to_url(str(tmp_path / "weird:scheme.md"))
    assert got.startswith("file://") and got.endswith("weird%3Ascheme.md")
