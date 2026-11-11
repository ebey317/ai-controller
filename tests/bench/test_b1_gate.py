"""B1: deterministic approval-gate benchmark at the existing slash seam.

Covers parser recognition and denial behavior only; intentionally does not claim
full application-entry-path coverage. All external/action paths are monkeypatched.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

# The runner registers process-wide signal handlers during import.
import signal  # noqa: E402

import sensei_slash as slash  # noqa: E402

_original_signal = signal.signal
try:
    signal.signal = lambda *args, **kwargs: None
    import tahuti_goal_runner as runner  # noqa: E402
finally:
    signal.signal = _original_signal


# These actual registry commands are gated by the current parser. In particular,
# only `x`, not bare `exit`/`quit`, is currently registered. `clear` resolves to
# its submenu, so use an exact leaf command when measuring direct-run gate denial.
GATED_UTTERANCES = (
    ("slash x", "/x"),
    ("slash restart", "/restart"),
    ("slash forget tea", "/forget: tea"),
    ("slash git commit benchmark", "/git commit benchmark"),
    ("slash hooks disable benchmark-hook", "/hooks disable benchmark-hook"),
)


@pytest.mark.parametrize(("utterance", "expected_send"), GATED_UTTERANCES)
def test_registered_gated_slash_command_parses_as_gated(utterance, expected_send):
    parsed = slash.parse(utterance)

    assert parsed is not None
    assert parsed["kind"] == "run"
    assert parsed["send"] == expected_send
    assert parsed["gated"] is True


@pytest.mark.parametrize(("utterance", "expected_send"), GATED_UTTERANCES)
def test_denied_gate_produces_no_action_at_process_slash_seam(
    utterance, expected_send, monkeypatch
):
    parsed = slash.parse(utterance)
    assert parsed is not None and parsed["kind"] == "run" and parsed["gated"]

    effects = []

    def denied(goal_id, pattern, description, denied_message):
        effects.append(("gate", goal_id, pattern, description, denied_message))
        return {"error": "approval denied"}

    monkeypatch.setattr(runner, "_gate_desktop_action", denied)
    monkeypatch.setattr(
        runner, "_run_slash_card", lambda *a, **k: effects.append(("card", a, k))
    )
    monkeypatch.setattr(
        runner,
        "_write_to_agent_thread",
        lambda *a, **k: effects.append(("thread", a, k)),
    )
    monkeypatch.setattr(
        slash, "sensei_lock", lambda *a, **k: pytest.fail("lock must not be acquired")
    )
    monkeypatch.setattr(
        slash, "ensure_sensei", lambda *a, **k: pytest.fail("Sensei must not start")
    )
    monkeypatch.setattr(
        slash, "run_in_sensei", lambda *a, **k: pytest.fail("command must not run")
    )
    monkeypatch.setenv("DISPLAY", ":b1-isolated")

    response, already_spoken = runner._process_slash("b1-goal", parsed)

    expected_pattern = f"tahuti:slash:{parsed['base'].split()[0]}"
    assert effects == [
        (
            "gate",
            "b1-goal",
            expected_pattern,
            f"Run {expected_send} in Sensei",
            f"{expected_send} was not run",
        )
    ]
    assert "approval denied" in response
    assert already_spoken is False
