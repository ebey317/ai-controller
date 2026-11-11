"""T25 — Tahuti direct 'open …' lane + fast-fallback timeout tests.

These tests never call a live model and never touch the operator's real
Tahuti channels (conftest's autouse sandbox fixture handles that).
"""

from __future__ import annotations

import json

import pytest
import tahuti_goal_runner as runner


@pytest.fixture(autouse=True)
def _fake_home(tmp_path_factory, monkeypatch):
    """Folder phrases resolve against REAL folders, so give every test a fake
    home that has them (CI has no ~/Desktop/Projects; a laptop must not
    depend on its own)."""
    home = tmp_path_factory.mktemp("fakehome")
    for d in ("Desktop/Projects", "projects", "Downloads", "Documents", "MD"):
        (home / d).mkdir(parents=True)
    op = runner.tahuti_operator
    monkeypatch.setattr(op, "HOME", home)
    monkeypatch.setattr(op, "PROFILE", home / ".tahuti" / "operator_profile.md")


# ──────────────────────────────────────────────────────────────────────
# Parser: simple cases and explicit rejections
# ──────────────────────────────────────────────────────────────────────


def test_parse_open_youtube_is_url():
    assert runner._parse_direct_open("open youtube") == ("url", "youtube.com")


def test_parse_please_open_my_projects_folder_is_folder():
    # The optional 'my' article is stripped; the tool still receives a
    # natural folder phrase.
    assert runner._parse_direct_open("please open my projects folder") == (
        "folder",
        "projects folder",
    )


def test_parse_open_projects_folder_on_desktop_is_folder():
    # 'on my desktop' is allowed for folders; tahuti_operator._candidates
    # resolves the real Desktop path.
    assert runner._parse_direct_open(
        "open the projects folder that's on my desktop"
    ) == ("folder", "projects folder that's on my desktop")


def test_parse_open_chrome_is_app():
    assert runner._parse_direct_open("open chrome") == ("app", "chrome")


def test_parse_open_youtube_and_search_is_none():
    # Multi-clause and lookup words force the model path.
    assert runner._parse_direct_open("open youtube and search for cats") is None


def test_parse_open_pod_bay_doors_is_none():
    # Unknown target; do not guess.
    assert runner._parse_direct_open("open the pod bay doors") is None


def test_parse_weather_is_none():
    assert runner._parse_direct_open("what's the weather") is None


def test_parse_bare_domain_is_url():
    assert runner._parse_direct_open("open foo.com") == ("url", "foo.com")


def test_parse_open_gmail_is_url():
    assert runner._parse_direct_open("go to gmail") == ("url", "mail.google.com")


def test_parse_open_x_is_url():
    assert runner._parse_direct_open("launch x") == ("url", "x.com")


# ──────────────────────────────────────────────────────────────────────
# Goal-level lane: never calls the model, archives deterministic-open
# ──────────────────────────────────────────────────────────────────────


def _direct_open_goal(tmp_path, monkeypatch, transcript):
    """Create a goal file and stub every real side channel."""
    goals = tmp_path / "goals"
    done = goals / "done"
    failed = goals / "failed"
    for d in (goals, done, failed):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(runner, "GOALS_DIR", goals)
    monkeypatch.setattr(runner, "DONE_DIR", done)
    monkeypatch.setattr(runner, "FAILED_DIR", failed)
    monkeypatch.setattr(runner, "_show_work_avatar", lambda: None)
    monkeypatch.setattr(runner, "_post_speak", lambda text: None)
    monkeypatch.setattr(runner, "_write_caption", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_notify", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_write_last_response", lambda *a, **k: None)
    monkeypatch.setattr(
        runner,
        "_call_model",
        lambda *a, **k: pytest.fail("direct-open lane must not call _call_model"),
    )

    goal_path = goals / "direct1.json"
    goal_path.write_text(
        json.dumps(
            {
                "id": "direct1",
                "transcript": transcript,
                "type": "task",
                "time": "t",
                "source": "test",
            }
        ),
        encoding="utf-8",
    )
    return goal_path


def test_direct_open_url_goal_skips_model_and_archives(monkeypatch, tmp_path):
    opened = []

    def fake_open_url(goal_id: str, url: str):
        opened.append((goal_id, url))
        return {"ok": True, "opened": url, "via": "mcp-tab"}

    monkeypatch.setattr(runner, "_open_url_tool", fake_open_url)
    monkeypatch.setattr(
        runner, "_open_app_tool", lambda *a, **k: pytest.fail("url lane")
    )
    monkeypatch.setattr(
        runner, "_open_folder_tool", lambda *a, **k: pytest.fail("url lane")
    )

    goal_path = _direct_open_goal(tmp_path, monkeypatch, "open youtube")
    runner._process_goal(goal_path)

    assert opened == [("direct1", "youtube.com")]
    done = sorted(runner.DONE_DIR.glob("*.json"))
    assert len(done) == 1
    rec = json.loads(done[0].read_text())
    assert rec["model"] == "deterministic-open"
    assert "Opened" in rec["response"]


def test_direct_open_app_goal_skips_model_and_archives(monkeypatch, tmp_path):
    opened = []

    def fake_open_app(goal_id: str, name: str):
        opened.append((goal_id, name))
        return {"ok": True, "opened": name}

    monkeypatch.setattr(runner, "_open_app_tool", fake_open_app)
    monkeypatch.setattr(
        runner, "_open_url_tool", lambda *a, **k: pytest.fail("app lane")
    )
    monkeypatch.setattr(
        runner, "_open_folder_tool", lambda *a, **k: pytest.fail("app lane")
    )

    goal_path = _direct_open_goal(tmp_path, monkeypatch, "open chrome")
    runner._process_goal(goal_path)

    assert opened == [("direct1", "chrome")]
    rec = json.loads((runner.DONE_DIR / "direct1.json").read_text())
    assert rec["model"] == "deterministic-open"
    assert "Opened" in rec["response"]


def test_direct_open_folder_goal_skips_model_and_archives(monkeypatch, tmp_path):
    opened = []

    def fake_open_folder(name: str):
        opened.append(name)
        return {"ok": True, "folder": str(tmp_path / "Projects")}

    monkeypatch.setattr(runner, "_open_folder_tool", fake_open_folder)
    monkeypatch.setattr(
        runner, "_open_url_tool", lambda *a, **k: pytest.fail("folder lane")
    )
    monkeypatch.setattr(
        runner, "_open_app_tool", lambda *a, **k: pytest.fail("folder lane")
    )

    goal_path = _direct_open_goal(tmp_path, monkeypatch, "open my projects folder")
    runner._process_goal(goal_path)

    assert opened == ["projects folder"]
    rec = json.loads((runner.DONE_DIR / "direct1.json").read_text())
    assert rec["model"] == "deterministic-open"
    assert "Opened" in rec["response"]


def test_direct_open_tool_error_is_spoken(monkeypatch, tmp_path):
    def fake_open_url(goal_id: str, url: str):
        return {"ok": False, "error": "the browser bridge isn't connected"}

    monkeypatch.setattr(runner, "_open_url_tool", fake_open_url)

    goal_path = _direct_open_goal(tmp_path, monkeypatch, "open github")
    runner._process_goal(goal_path)

    rec = json.loads((runner.DONE_DIR / "direct1.json").read_text())
    assert rec["model"] == "deterministic-open"
    assert "bridge" in rec["response"]


# ──────────────────────────────────────────────────────────────────────
# Fallback timeout: fast routes fail fast
# ──────────────────────────────────────────────────────────────────────


def test_fast_fallback_uses_20s_timeout(monkeypatch):
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["timeout"] = timeout
        raise RuntimeError("network down")

    monkeypatch.setattr(runner.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(runner, "_load_openrouter_key", lambda: "fake-key")

    runner._call_free_fallback("hi", 0.0, route="fast-chat")
    assert captured["timeout"] == 20


def test_non_fast_fallback_uses_120s_timeout(monkeypatch):
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["timeout"] = timeout
        raise RuntimeError("network down")

    monkeypatch.setattr(runner.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(runner, "_load_openrouter_key", lambda: "fake-key")

    runner._call_free_fallback("hi", 0.0, route="wonder-multi")
    assert captured["timeout"] == 120


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
