"""B4 network safety benchmark: isolated SSRF and redirect guard checks.

All DNS and HTTP are stubbed; these tests never access the network or desktop.
"""
from __future__ import annotations

import importlib
import socket
import signal
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# The runner registers SIGINT/SIGTERM handlers at import time. Suppress that
# process-global side effect while loading it; all benchmark I/O is then stubbed.
_real_signal = signal.signal
try:
    signal.signal = lambda *args, **kwargs: None
    runner = importlib.import_module("tahuti_goal_runner")
finally:
    signal.signal = _real_signal

PUBLIC_IP = "93.184.216.34"


def _addr(ip: str):
    return (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))


def test_resolve_public_ip_rejects_private_and_mixed_answers(monkeypatch):
    for answers in ([ _addr("10.0.0.1") ], [_addr(PUBLIC_IP), _addr("127.0.0.1")]):
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, _answers=answers, **k: _answers)
        assert runner._resolve_public_ip("example.test", 443) is None


def test_resolve_public_ip_returns_public_address_and_prefers_ipv4(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:2800:220:1:248:1893:25c8:1946", 443, 0, 0)),
        _addr(PUBLIC_IP),
    ])
    assert runner._resolve_public_ip("example.test", 443) == PUBLIC_IP


class _Response:
    def __init__(self, status=200, headers=None):
        self.status_code = status
        self.headers = headers or {"content-type": "text/html; charset=utf-8"}
        self.encoding = "utf-8"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_bytes(self):
        yield b"<html><title>ok</title><body>public content</body></html>"


class _Client:
    def __init__(self, responses, **kwargs):
        self.responses = responses
        self.kwargs = kwargs
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def stream(self, method, target, **kwargs):
        self.calls.append((method, target, kwargs))
        return self.responses.pop(0)


def _install_fakes(monkeypatch, ips, responses):
    dns_calls = []

    def fake_dns(host, port, **kwargs):
        dns_calls.append((host, port))
        ip = ips.pop(0)
        if ip is None:
            raise AssertionError("DNS attempted after expected refusal")
        return [_addr(ip)]

    clients = []

    def fake_client(**kwargs):
        client = _Client(responses, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(socket, "getaddrinfo", fake_dns)
    monkeypatch.setattr(runner.httpx, "Client", fake_client)
    return dns_calls, clients


def test_read_page_pins_public_ip_ignores_proxy_and_preserves_https_host_sni(monkeypatch):
    dns, clients = _install_fakes(monkeypatch, [PUBLIC_IP], [_Response()])
    result = runner._read_page_tool("https://www.example.test/path")
    assert result["ok"] is True
    assert dns == [("www.example.test", 443)]
    client = clients[0]
    assert client.kwargs["trust_env"] is False
    assert client.kwargs["follow_redirects"] is False
    _, target, call = client.calls[0]
    assert target == "https://93.184.216.34/path"
    assert call["headers"]["Host"] == "www.example.test"
    assert call["extensions"] == {"sni_hostname": "www.example.test"}


def test_read_page_refuses_public_redirect_to_private_before_http(monkeypatch):
    dns, clients = _install_fakes(
        monkeypatch, [PUBLIC_IP, "10.0.0.1"],
        [_Response(302, {"location": "http://internal.example.test/admin"})],
    )
    result = runner._read_page_tool("https://www.example.test/")
    assert result["ok"] is False
    assert "non-public address" in result["error"]
    assert dns == [("www.example.test", 443), ("internal.example.test", 80)]
    assert len(clients) == 1


def test_read_page_enforces_redirect_hop_limit(monkeypatch):
    redirects = [_Response(302, {"location": f"/hop{i}"}) for i in range(5)]
    dns, clients = _install_fakes(monkeypatch, [PUBLIC_IP] * 5, redirects)
    result = runner._read_page_tool("https://www.example.test/start")
    assert result == {"ok": False, "error": "too many redirects"}
    assert len(dns) == 5
    assert len(clients) == 5


@pytest.mark.parametrize("ip", [
    "10.1.2.3",       # RFC1918 private
    "127.0.0.1",      # loopback
    "169.254.1.2",    # link-local
    "224.0.0.1",      # multicast
    "240.0.0.1",      # reserved
    "192.0.2.1",      # documentation/reserved
    "::1",            # IPv6 loopback
    "fc00::1",        # IPv6 unique-local
    "fe80::1",        # IPv6 link-local
    "ff02::1",        # IPv6 multicast
    "2001:db8::1",    # IPv6 documentation/reserved
])
def test_resolve_public_ip_rejects_non_public_address_classes(monkeypatch, ip):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    sockaddr = (ip, 443, 0, 0) if family == socket.AF_INET6 else (ip, 443)
    answer = (family, socket.SOCK_STREAM, 6, "", sockaddr)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [answer])

    assert runner._resolve_public_ip("example.test", 443) is None


def test_resolve_public_ip_rejects_dns_failure_and_empty_answer(monkeypatch):
    def fail_dns(*args, **kwargs):
        raise socket.gaierror("stubbed lookup failure")

    monkeypatch.setattr(socket, "getaddrinfo", fail_dns)
    assert runner._resolve_public_ip("missing.example.test", 443) is None

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [])
    assert runner._resolve_public_ip("empty.example.test", 443) is None


def test_read_page_connects_to_validated_ip_if_dns_would_rebind(monkeypatch):
    # Return one public DNS answer, then fail if anything tries to resolve again.
    dns, clients = _install_fakes(monkeypatch, [PUBLIC_IP], [_Response()])
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:3128")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:3128")

    result = runner._read_page_tool("https://www.example.test/path")

    assert result["ok"] is True
    assert dns == [("www.example.test", 443)]
    assert clients[0].kwargs["trust_env"] is False
    assert clients[0].calls[0][1] == "https://93.184.216.34/path"


def test_read_page_follows_public_redirect_and_rechecks_target(monkeypatch):
    dns, clients = _install_fakes(
        monkeypatch, [PUBLIC_IP, "1.1.1.1"],
        [_Response(302, {"location": "https://cdn.example.test/asset"}), _Response()],
    )

    result = runner._read_page_tool("https://www.example.test/start")

    assert result["ok"] is True
    assert dns == [("www.example.test", 443), ("cdn.example.test", 443)]
    assert len(clients) == 2
    assert clients[1].calls[0][1] == "https://1.1.1.1/asset"

