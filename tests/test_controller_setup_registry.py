"""Tests for the setup registry spec-review gaps.

These tests are hermetic: every check that normally touches the live
machine (subprocess, HTTP, filesystem) is monkeypatched so the suite
remains fast and safe.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import controller_setup as cs  # noqa: E402

# ── registry category coverage ────────────────────────────────────────


def test_backends_cover_all_required_categories():
    required = {
        "controller",
        "audio-voice",
        "tahuti",
        "sensei-master-ai",
        "services",
        "integrations",
        "diagnostics",
    }
    found = {b.category for b in cs.BACKENDS}
    missing = required - found
    assert not missing, f"missing categories: {missing}"


def test_backend_names_are_unique():
    names = [b.name for b in cs.BACKENDS]
    assert len(names) == len(set(names))


def test_backend_status_returns_source_category_and_result():
    source, category, result = cs.backend_status("voice-profiles")
    assert source == "live"
    assert category == "controller"
    assert result[0] in (cs.OK, cs.WARN, cs.FAIL)


def test_backend_status_unknown_returns_none():
    assert cs.backend_status("nonexistent-backend") == (None, None, None)


# ── services must be a registry backend ───────────────────────────────


def test_services_is_registry_backend():
    names = [b.name for b in cs.BACKENDS]
    assert "services" in names
    funcs = [b.check_fn for b in cs.BACKENDS]
    assert cs.check_services in funcs


def test_check_services_monkeypatched_subprocess(monkeypatch):
    class FakeResult:
        stdout = "active\n"
        stderr = ""
        returncode = 0

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        assert cmd[0] == "systemctl"
        assert cmd[1] == "--user"
        return FakeResult()

    monkeypatch.setattr(cs.subprocess, "run", fake_run)
    rows = cs.check_services()
    assert len(rows) == len(cs.SERVICES)
    assert all(level == cs.OK for level, _, _ in rows)
    assert len(calls) == len(cs.SERVICES)


def test_check_services_reports_inactive(monkeypatch):
    class FakeResult:
        stdout = "inactive\n"
        stderr = ""
        returncode = 3

    def fake_run(cmd, **kwargs):
        return FakeResult()

    monkeypatch.setattr(cs.subprocess, "run", fake_run)
    rows = cs.check_services()
    assert any(level == cs.WARN for level, label, _ in rows)


# ── Tahuti: honest static-source-presence reporting ─────────────────


def _fake_script_path(tmp_path, present_names):
    for n in present_names:
        (tmp_path / n).write_text("# present\n")

    def _fn(name):
        return str(tmp_path / name)

    return _fn


def test_tahuti_desktop_warns_on_source_presence(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cs,
        "_script_path",
        _fake_script_path(tmp_path, ["tahuti_desktop.py"]),
    )
    level, label, detail = cs.check_tahuti_desktop()
    assert level == cs.WARN
    assert label == "tahuti desktop"
    assert "static source presence is not live verification" in detail


def test_tahuti_desktop_fails_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "_script_path", lambda name: str(tmp_path / name))
    level, _, detail = cs.check_tahuti_desktop()
    assert level == cs.FAIL
    assert "missing" in detail.lower()


def test_tahuti_mcp_warns_on_source_presence(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cs,
        "_script_path",
        _fake_script_path(tmp_path, ["tahuti_mcp.py"]),
    )
    level, label, detail = cs.check_tahuti_mcp()
    assert level == cs.WARN
    assert label == "tahuti mcp"
    assert "static source presence is not live verification" in detail


def test_tahuti_mcp_fails_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "_script_path", lambda name: str(tmp_path / name))
    level, _, detail = cs.check_tahuti_mcp()
    assert level == cs.FAIL
    assert "missing" in detail.lower()


# ── Sensei bridge: GET /health with response validation ─────────────


def test_sensei_bridge_gets_health_and_validates_response(monkeypatch):
    payload = {
        "ok": True,
        "service": "sensei_bridge",
        "model": "test-model",
        "vision_model": "test-vision",
        "ollama": "http://localhost:11434",
    }

    class FakeResp:
        def read(self):
            return json.dumps(payload).encode("utf-8")

    class FakeUrlopen:
        def __init__(self, url, timeout=None):
            self.url = url
            self._resp = FakeResp()

        def __enter__(self):
            return self._resp

        def __exit__(self, *args):
            return False

    def fake_urlopen(url, timeout=None):
        assert str(url) == "http://127.0.0.1:8791/health"
        return FakeUrlopen(url, timeout)

    monkeypatch.setattr(cs.urllib.request, "urlopen", fake_urlopen)
    level, label, detail = cs.check_sensei_bridge()
    assert level == cs.OK
    assert label == "sensei bridge"
    assert "/health" in detail
    assert "test-model" in detail
    assert "test-vision" in detail


def test_sensei_bridge_warns_when_ok_is_false(monkeypatch):
    class FakeResp:
        def read(self):
            return json.dumps({"ok": False}).encode("utf-8")

    class FakeUrlopen:
        def __init__(self, url, timeout=None):
            pass

        def __enter__(self):
            return FakeResp()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(cs.urllib.request, "urlopen", FakeUrlopen)
    level, _, detail = cs.check_sensei_bridge()
    assert level == cs.WARN
    assert "ok=False" in detail


def test_sensei_bridge_warns_on_unreachable_health(monkeypatch):
    def fake_urlopen(url, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(cs.urllib.request, "urlopen", fake_urlopen)
    level, _, detail = cs.check_sensei_bridge()
    assert level == cs.WARN
    assert "8791/health" in detail


# ── Diagnostics: bounded read-only probe, honest reporting ───────────


def test_diagnostics_passes_syntax_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cs,
        "_script_path",
        _fake_script_path(tmp_path, ["voice_stt_health_check.py"]),
    )
    level, label, detail = cs.check_diagnostics()
    assert level == cs.WARN
    assert label == "diagnostics"
    assert "static source presence is not live verification" in detail


def test_diagnostics_fails_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "_script_path", lambda name: str(tmp_path / name))
    level, _, detail = cs.check_diagnostics()
    assert level == cs.FAIL
    assert "missing" in detail.lower()


def test_diagnostics_fails_on_syntax_error(tmp_path, monkeypatch):
    (tmp_path / "voice_stt_health_check.py").write_text("def bad(\n")
    monkeypatch.setattr(
        cs,
        "_script_path",
        lambda name: str(tmp_path / name),
    )
    level, _, detail = cs.check_diagnostics()
    assert level == cs.FAIL
    assert "syntax" in detail.lower()


# ── run_doctor failure containment ───────────────────────────────────


def test_run_doctor_contains_check_exception(monkeypatch):
    def boom():
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(
        cs,
        "BACKENDS",
        [cs.Backend("crasher", "controller", "live", boom)],
    )
    rows = cs.run_doctor()
    assert len(rows) == 1
    level, label, detail = rows[0]
    assert level == cs.FAIL
    assert label == "crasher"
    assert "simulated crash" in detail


def test_run_doctor_suppresses_info_rows(monkeypatch):
    def info_backend():
        return ("INFO", "info-test", "should be filtered")

    monkeypatch.setattr(
        cs,
        "BACKENDS",
        [cs.Backend("info-test", "controller", "live", info_backend)],
    )
    rows = cs.run_doctor()
    assert rows == []


def test_backend_status_contains_check_exception(monkeypatch):
    def boom():
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(
        cs,
        "BACKENDS",
        [cs.Backend("crasher", "controller", "live", boom)],
    )
    source, category, result = cs.backend_status("crasher")
    assert source == "live"
    assert category == "controller"
    assert result == (cs.FAIL, "crasher", "check crashed: simulated crash")


def test_registry_rejects_unknown_backend_category():
    bad = cs.Backend("bad-cat", "not-a-category", "live", lambda: (cs.OK, "", ""))
    with pytest.raises(ValueError, match="not-a-category"):
        cs._validate_backends([bad], cs.CATEGORIES)
