"""OpenCode Zen AND Go both need x-opencode-session (Go failed with
HTTP 400 MissingSessionID because only the Zen URL got the header)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import sensei_model as sm  # noqa: E402


class _Boom(Exception):
    pass


@pytest.mark.parametrize("provider", ["opencode-go", "opencode", "opencode-free"])
def test_opencode_lanes_send_the_session_header(monkeypatch, provider):
    seen = {}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            seen.update(headers or {})
            raise _Boom()

    monkeypatch.setattr(sm.httpx, "Client", FakeClient)
    monkeypatch.setattr(sm, "find_key", lambda names: "k")
    with pytest.raises(sm.ProviderError):
        sm.run((provider, "m"), "sys", [], "hi", [], lambda c: {})
    assert seen.get("x-opencode-session")


def test_other_providers_do_not_get_the_session_header(monkeypatch):
    seen = {}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            seen.update(headers or {})
            raise _Boom()

    monkeypatch.setattr(sm.httpx, "Client", FakeClient)
    monkeypatch.setattr(sm, "find_key", lambda names: "k")
    with pytest.raises(sm.ProviderError):
        sm.run(("openrouter", "m"), "sys", [], "hi", [], lambda c: {})
    assert "x-opencode-session" not in seen
