"""Round-5 review tests (2026-11-05) — the 7 items + 2 cheap bench items in
round5_ai-controller.md, each through the REAL code path:

 1  _sensei_process_exists — genuinely different check (tmux-server match via
    /proc environ), never a repeat of sensei_pane's pane-tree walk.
 2+3 _resolve_public_ip — safety check on the UNWRAPPED IPv4, connect pinned
    to the ORIGINAL address; NAT64 is the /96 network only.
 4  ptt on_release — the take counter advances ONLY on agent takes.
 5  send_to_chat.needs_enter — the user's own pending text is never "busy".
 6  install.sh — nudge-timer exception documented; warning prints the command.
 7  INSTALL.md — no stray blank inside a numbered list; blank line before ---.
 A  bench A2 — stages["routed"] stamped at the LANE decision, not after claim.
 B  bench --json echoes the --dir actually passed.
"""

from __future__ import annotations

import ipaddress
import ast
import ipaddress
import json
import socket
import subprocess
import sys
import types
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import bench_speed_report as bsr  # noqa: E402
import send_to_chat  # noqa: E402
import sensei_slash as sb  # noqa: E402
import tahuti_goal_runner as runner  # noqa: E402

PTT_SRC = SCRIPTS / "ptt_pynput.py"


# ── Item 1: _sensei_process_exists must NOT repeat sensei_pane's walk ────────


def _fake_tmux(has_session=0, server_pid="555", recorded=None):
    def fake(*args, **kw):
        if recorded is not None:
            recorded.append(args)
        if "has-session" in args:
            return subprocess.CompletedProcess(args, has_session)
        if "display" in args:
            return subprocess.CompletedProcess(args, 0, stdout=f"{server_pid}\n")
        return subprocess.CompletedProcess(args, 0, stdout="")

    return fake


def test_sensei_process_exists_matches_the_session_tmux_server(monkeypatch):
    monkeypatch.setattr(sb, "session_name", lambda: "tahuti")
    monkeypatch.setattr(sb, "_tmux", _fake_tmux())
    monkeypatch.setattr(
        sb.subprocess,
        "run",
        lambda a, **k: subprocess.CompletedProcess(
            a, 0, stdout="123\n" if a and a[0] == "pgrep" else ""
        ),
    )
    ours = {
        "pid-123": "PATH=/bin\nTMUX=/tmp/tmux-1000/default,555,0\n",
        "pid-456": "PATH=/bin\nTMUX=/tmp/tmux-1000/default,999,0\n",
    }
    monkeypatch.setattr(sb, "_proc_environ", lambda pid: ours.get(f"pid-{pid}", ""))

    assert sb._sensei_process_exists() is True  # 123 belongs to OUR server
    assert sb._sensei_process_exists("tahuti") is True


def test_sensei_process_exists_refuses_other_servers(monkeypatch):
    monkeypatch.setattr(sb, "session_name", lambda: "tahuti")
    monkeypatch.setattr(sb, "_tmux", _fake_tmux())
    monkeypatch.setattr(
        sb.subprocess,
        "run",
        lambda a, **k: subprocess.CompletedProcess(
            a, 0, stdout="123\n" if a and a[0] == "pgrep" else ""
        ),
    )
    # BOTH processes live on OTHER tmux servers — nothing of ours.
    monkeypatch.setattr(
        sb, "_proc_environ", lambda pid: "PATH=/bin\nTMUX=/tmp/tmux-1000/other,777,0\n"
    )
    assert sb._sensei_process_exists() is False

    # vanished / unreadable environ → not ours
    monkeypatch.setattr(sb, "_proc_environ", lambda pid: "")
    assert sb._sensei_process_exists() is False


def test_sensei_process_exists_is_a_different_check_than_sensei_pane(monkeypatch):
    """The round-5 point: this must NOT be a second pane-tree walk. The
    single real difference that makes the ensure_sensei guard able to fire."""
    calls: list[tuple] = []
    monkeypatch.setattr(sb, "session_name", lambda: "tahuti")
    monkeypatch.setattr(sb, "_tmux", _fake_tmux(recorded=calls))
    monkeypatch.setattr(
        sb.subprocess,
        "run",
        lambda a, **k: subprocess.CompletedProcess(
            a, 0, stdout="123\n" if a and a[0] == "pgrep" else ""
        ),
    )
    monkeypatch.setattr(
        sb, "_proc_environ", lambda pid: "TMUX=/tmp/tmux-1000/default,555,0\n"
    )
    assert sb._sensei_process_exists() is True
    assert not any(
        a[0] == "list-panes" for a in calls
    ), "the check re-walked the panes like sensei_pane does"
    assert any(a[0] == "display" for a in calls)  # asked the SERVER pid


def test_sensei_process_exists_needs_a_session(monkeypatch):
    monkeypatch.setattr(sb, "session_name", lambda: "tahuti")
    monkeypatch.setattr(sb, "_tmux", _fake_tmux(has_session=1))
    assert sb._sensei_process_exists() is False
    assert sb._sensei_process_exists("some-missing-session") is False


# ── Items 2+3: _resolve_public_ip through the REAL function ──────────────────


def _addrinfo(addr: str, port: int = 443):  # AF_INET6-ish tuple shape
    return [(10, 1, 6, "", (addr, port, 0, 0))]


@pytest.mark.parametrize(
    "addr,want",
    [
        ("::ffff:127.0.0.1", None),  # mapped loopback: refused
        ("::ffff:808:808", "::ffff:808:808"),  # == ::ffff:8.8.8.8 — ORIGINAL pinned
        ("64:ff9b::7f00:1", None),  # NAT64-embedded loopback: refused
        ("64:ff9b::808:808", "64:ff9b::808:808"),  # NAT64 public: ORIGINAL pinned
        ("64:ff9b:1::808:808", None),  # /32 impostor: no unwrap, refused as-is
        #   (the old >>96 test unwrapped this to 8.8.8.8 and handed it out)
        (
            "2001:0:4136:e378:8000:63bf:f7f7:f7f7",
            "2001:0:4136:e378:8000:63bf:f7f7:f7f7",
        ),  # Teredo w/ public client: ORIGINAL
        ("100.64.1.1", None),  # CGNAT
        ("224.0.0.1", None),
        ("ff02::1", None),  # never connect targets (B4)
        ("8.8.8.8", "8.8.8.8"),
    ],
)
def test_resolve_public_ip_unwraps_for_safety_pins_the_original(
    monkeypatch, addr, want
):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, port, type=0: _addrinfo(addr)
    )
    got = runner._resolve_public_ip("evil.example.test", 443)
    # Compare as addresses: Python 3.9 prints ::ffff:808:808, 3.10+ ::ffff:8.8.8.8.
    assert (got is None) == (want is None)
    if want is not None:
        assert ipaddress.ip_address(got) == ipaddress.ip_address(want)


def test_resolve_public_ip_rejects_mapped_loopback_directly(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, port, type=0: _addrinfo("::ffff:127.0.0.1")
    )
    assert runner._resolve_public_ip("evil.example.test", 443) is None


# ── Item 4: the take counter advances ONLY on agent takes (real path) ────────


class _QuietLog:
    warning = staticmethod(lambda *a, **k: None)  # type: ignore[attr-defined]
    info = warning
    debug = warning
    error = warning


@pytest.fixture()
def r5_rig(tmp_path):
    """The round-4 real-path rig, rebuilt around the round-5 rule: the release
    counter and the caption guards only advance on agent takes."""
    capfile = tmp_path / "caption.json"
    clock = {"t": 300.0}

    def tick():
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
    missing = want - {getattr(n, "name", "") for n in tree.body}
    assert not missing, f"extraction lost {missing}"

    jobs: list[tuple] = []

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
        "os": __import__("os"),
        "time": types.SimpleNamespace(time=tick),
        "threading": types.SimpleNamespace(Thread=_SyncThread),
        "keyboard": types.SimpleNamespace(
            Key=types.SimpleNamespace(f13=object(), ctrl_l=object(), ctrl_r=object())
        ),
        "avatar_pulse": pulse_stub,
        "_stop_and_send_impl": lambda *a, **k: None,
        "_ctrl_held": False,
        "_goal_take": False,
        "take_seq": 0,
    }
    exec(compile("\n".join(chunks), str(PTT_SRC), "exec"), ns)  # noqa: S102
    return ns, capfile, jobs, killed, clock


def r5_caption(capfile: Path) -> dict:
    return json.loads(capfile.read_text())


def test_dictation_release_does_not_bump_the_take_counter(r5_rig):
    ns, _cap, jobs, _killed, _clock = r5_rig
    ns["on_release"](ns["keyboard"].Key.f13)  # dictation: kill pulse
    t, a = jobs.pop(0)
    assert a[0] is False and a[2] == 0  # not an agent take id
    ns["_goal_take"] = True
    ns["on_release"](ns["keyboard"].Key.f13)  # agent take owns id 1
    t, a = jobs.pop(0)
    assert a[0] is True and a[2] == 1 and ns["take_seq"] == 1


def test_late_agent_take_clears_despite_intervening_dictation(r5_rig):
    """The round-5 regression: agent take stamps 'sending' → a later
    DICTATION release used to bump the counter and orphan the agent take —
    its finally never cleared the caption. Now dictation cannot."""
    ns, capfile, jobs, _killed, _clock = r5_rig
    ns["_goal_take"] = True
    ns["on_release"](ns["keyboard"].Key.f13)  # agent take, 'sending'
    agent = jobs.pop(0)
    ns["_goal_take"] = False
    ns["on_release"](ns["keyboard"].Key.f13)  # a later DICTATION take
    jobs.pop(0)  # never touches the counter
    assert r5_caption(capfile)["state"] == "sending"
    t, a = agent
    t(*a)  # the agent finally runs late
    assert (
        r5_caption(capfile)["state"] == "idle"
    ), "dictation took the agent take's counter slot — 'sending' stuck again"


def test_two_takes_still_own_distinct_ids(r5_rig):
    ns, _cap, jobs, _killed, _clock = r5_rig
    ns["_goal_take"] = True
    ns["on_release"](ns["keyboard"].Key.f13)
    ns["on_release"](ns["keyboard"].Key.f13)
    assert jobs[-2][1][2] == 1 and jobs[-1][1][2] == 2, "agent ids must stay distinct"


# ── Item 5: the user's own pending text is never busy ───────────────────────


def test_own_pending_text_containing_running_still_gets_enter():
    message = "running out of time, remind me later"
    tail = ["ashes to dust", f"➤ {message}", ""]  # my text echoed in the box
    assert (
        send_to_chat.needs_enter(tail, message) is True
    ), "the user's own 'running…' text blocked the submit (round-5 #5)"


def test_agent_activity_line_still_counts_as_busy():
    message = "running out of time, remind me later"
    # my text echoed AND a real activity line elsewhere in the tail
    busy_tail = [f"➤ {message}", "● Running tools for the task", ""]
    assert send_to_chat.needs_enter(busy_tail, message) is False


def test_busy_markers_still_block():
    message = "check the calendar"
    for line in (
        "esc interrupt mid-turn",
        "_ctrl+c cancel_",
        "pondering the question",
        "waiting on: build",
        "msg=interrupt",
        "type to redirect",
    ):
        tail = [f"➤ {message}", line]
        assert send_to_chat.needs_enter(tail, message) is False, line


def test_generic_prose_running_without_my_text_is_not_busy():
    """Narrowed pattern: bare 'running' in ordinary prose no longer blocks —
    only a status line (marker + Running) does."""
    tail = [
        "➤ hey what is the plan",  # my text sits in the box
        "I was Running analysis earlier",
    ]  # prose, NOT an activity line
    assert send_to_chat.needs_enter(tail, "hey what is the plan") is True


# ── Item 6: the nudge timer exception is documented, warning prints the cmd ──


def test_install_nudge_timer_exception_is_documented():
    src = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert (
        "ONE EXCEPTION" in src
    ), "section-10 comment must name the nudge timer exception"
    assert "tahuti-readme-nudge" in src.split("ONE EXCEPTION")[-1].split("# ── 10a")[0]
    # the failure warning prints the REAL command, not a truncated fragment
    assert (
        "enable it manually: systemctl --user enable --now tahuti-readme-nudge.timer"
        in src
    )
    assert "daemon-reload 2>/dev/null ||" in src  # round-4 guard still stands
    r = subprocess.run(
        ["bash", "-n", str(ROOT / "install.sh")], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr


# ── Item 7: INSTALL.md list integrity + heading separator ───────────────────


def test_install_md_numbered_list_has_no_stray_blank():
    md = (ROOT / "INSTALL.md").read_text(encoding="utf-8")
    lines = [ln for ln in md.splitlines()]
    # the first numbered list is contiguous — no blank line between its items
    start = next(i for i, ln in enumerate(lines) if ln.startswith("1. "))
    end = next(i for i, ln in enumerate(lines[start:], start) if ln.startswith("## "))
    inside = lines[start:end]
    item_next = tuple(f"{n}. " for n in range(1, 30))
    strays = [
        inside[i]
        for i, ln in enumerate(inside[:-1])
        if not ln.strip() and inside[i + 1].startswith(item_next)
    ]
    assert not strays, f"a blank line broke a numbered list again near: {strays}"

    # blank line between the README link and the trailing --- (a bare `---`
    # right after text would markdown-parse the text as a setext heading)
    pos = md.find("**Full documentation:**")
    seg = md[pos : pos + 200]
    assert "\n\n---" in seg, f"the `---` follows a text line directly: {seg!r}"


# ── Cheap A: stages["routed"] is stamped at the LANE decision ────────────────


def _run_show_lane(monkeypatch, tmp_path, transcript="show the inbox"):
    """Drive the REAL _process_goal through the show lane, capture stages."""
    goalfile = tmp_path / "g-show.json"
    goalfile.write_text(
        json.dumps(
            {
                "id": "g-show",
                "transcript": transcript,
                "type": "show",
                "time": 1234567.0,
                "source": "voice",
            }
        )
    )
    archived: dict = {}

    monkeypatch.setattr(runner, "_show_work_avatar", lambda: None)
    monkeypatch.setattr(runner, "_process_show", lambda: "here is the inbox")
    monkeypatch.setattr(runner, "_notify", lambda *a, **k: None)
    monkeypatch.setattr(
        runner,
        "_archive",
        lambda *a, **k: archived.update(stages=k.get("stages") or {}),
    )
    monkeypatch.setattr(runner, "_write_caption", lambda *a, **k: None)
    monkeypatch.setattr(runner, "DONE_DIR", tmp_path / "done")
    monkeypatch.setattr(runner, "FAILED_DIR", tmp_path / "failed")
    monkeypatch.setattr(runner, "_kill_avatar_pulse", lambda: None)
    # LAST_RESPONSE_FILE does not exist in the fake tree -> show reads "nothing yet"
    monkeypatch.setattr(runner, "LAST_RESPONSE_FILE", tmp_path / "no-such-response")

    runner._process_goal(goalfile)
    return archived.get("stages") or {}


def test_routed_stamped_after_lane_decision_not_after_claim(monkeypatch, tmp_path):
    stages = _run_show_lane(monkeypatch, tmp_path)
    assert (
        stages["routed"] > stages["claimed"]
    ), f"routed == claimed again (old bug: A2 measured nothing): {stages}"


def test_routed_stamped_at_each_lane_branch():
    """AST: every lane branch stamps the route — the model/ approval lanes too,
    not just the show branch the live test drives."""
    src = (SCRIPTS / "tahuti_goal_runner.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    goal = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_process_goal"
    )
    calls = [
        n
        for n in ast.walk(goal)
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_routed"
    ]
    # one per lane branch: show, time, slash, approval, model
    assert len(calls) >= 4, f"lane branches missing the route stamp: {len(calls)}"
    assert 'stages["routed"] = time.time()' not in src.replace(
        "setdefault", "sentinel"
    ), "the old inline stamp right after claimed came back"


def test_bench_report_a2_window_is_claimed_to_lane():
    # the STAGES table still measures A2 = claimed -> routed (unchanged names)
    names = {s[0]: (s[1], s[2]) for s in bsr.STAGES}
    assert names["A2"] == ("claimed", "routed")


# ── Cheap B: --json echoes the directory actually passed ─────────────────────


def test_report_json_echoes_the_passed_dir(tmp_path, capsys):
    empty = tmp_path / "elsewhere"
    empty.mkdir()
    assert bsr.report([], as_json=True, archive=empty) in (0, 1)
    data = json.loads(capsys.readouterr().out)
    assert data["archive"] == str(empty)


def test_main_json_honors_dir_flag(tmp_path, monkeypatch, capsys):
    arch = tmp_path / "goals"
    (arch / "done").mkdir(parents=True)
    (arch / "done" / "g1.json").write_text(
        json.dumps(
            {
                "id": "g1",
                "transcript": "t",
                "type": "show",
                "time": 100.0,
                "source": "voice",
                "response": "r",
                "model": "x",
                "stages": {
                    "goal_time": 100.0,
                    "claimed": 101.0,
                    "routed": 101.1,
                    "answer": 102.0,
                    "spoken": 102.5,
                },
            }
        )
    )
    bsr.main(["--dir", str(arch), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["archive"] == str(arch), "the --json output lied about the dir"
    assert data["goals"] == 1
    a2 = next(r for r in data["rows"] if r["stage"] == "A2")
    assert (
        a2["p95"] == 0.1 and a2["verdict"] == "PASS"
    ), "A2 must be measured as claimed -> 0.1 s route (target 0.2)"
