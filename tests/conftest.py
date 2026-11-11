"""Pytest bootstrap: make both import conventions work.

Working convention in this repo is bare imports with an explicit
sys.path insert per test module (see test_tahuti_goal_runner.py).
Newer files use ``from scripts.x import ...`` (repo-root convention).
This conftest puts the repo root on sys.path so both styles collect.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# CI / fresh machine: Sensei (~/master-ai-cli) is not beside this repo. The slash
# parser needs its command registry, so fall back to a frozen copy for TESTS only.
# On the operator's machine the real registry is used.
def _use_frozen_registry_when_sensei_absent():
    scripts = os.path.join(_ROOT, "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import pathlib

    import sensei_slash

    if not sensei_slash.REGISTRY_PATH.exists():
        sensei_slash.REGISTRY_PATH = (
            pathlib.Path(_ROOT) / "tests" / "fixtures" / "command_registry.py"
        )


_use_frozen_registry_when_sensei_absent()


# ── Tests must never touch the operator's real Tahuti ─────────────────────────
# 2026-11-05: a test ("show the inbox", answer "here is the inbox") POSTed to the
# REAL voice bridge, so every suite run SPOKE aloud, and each run dropped ~16 files
# into the real ~/.tahuti/thread_output. Isolate every real-world side channel the
# goal runner has: speech bridge, thread output, caption file, history, grants.
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _tahuti_runner_is_sandboxed(tmp_path_factory, monkeypatch):
    try:
        import tahuti_goal_runner as runner
    except (
        Exception
    ):  # modules that cannot import in this env (e.g. no GTK) are skipped
        yield
        return
    monkeypatch.setattr(
        runner, "BRIDGE_URL", "http://127.0.0.1:9"
    )  # closed port: never speaks
    sandbox = tmp_path_factory.mktemp("tahuti-sandbox")  # not the test's own tmp_path
    monkeypatch.setattr(runner, "_AGENT_THREAD_DIR", sandbox / "thread_output")
    monkeypatch.setattr(runner, "CAPTION_FILE", sandbox / "tahuti_caption.json")
    monkeypatch.setattr(
        runner, "CONVERSATION_HISTORY_FILE", sandbox / "conversation_history.json"
    )
    monkeypatch.setattr(runner, "GRANTS_FILE", sandbox / "grants.json")
    # The operator's REAL Sensei model pin must never steer a test (a valid pin
    # sends the runner down the pinned-provider path and hits the network).
    # active_pin's default path is bound at def time, so patch the default.
    monkeypatch.setattr(
        runner.sensei_model.active_pin, "__defaults__", (sandbox / "no_pin",)
    )
    yield
