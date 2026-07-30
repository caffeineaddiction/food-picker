"""The optional Cloudflare tunnel: URL scraping, failure paths, teardown.

Driven with a stub binary rather than the real thing — a test suite must never
publish the machine it runs on to the internet.
"""

from __future__ import annotations

import os
import stat

import pytest

from server import tunnel as tunnel_module
from server.tunnel import Tunnel, is_available

REAL_BANNER = """
2024-01-01T00:00:00Z INF Requesting new quick Tunnel on trycloudflare.com...
2024-01-01T00:00:00Z INF +--------------------------------------------------------+
2024-01-01T00:00:00Z INF |  Your quick Tunnel has been created! Visit it at:       |
2024-01-01T00:00:00Z INF |  https://brave-purple-horse-42.trycloudflare.com        |
2024-01-01T00:00:00Z INF +--------------------------------------------------------+
"""


def make_stub(tmp_path, body: str, *, exit_code: int = 0) -> str:
    script = tmp_path / "fake-cloudflared"
    script.write_text(f"#!/bin/sh\n{body}\nexit {exit_code}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def test_url_pattern_matches_real_cloudflared_output():
    match = tunnel_module.URL_PATTERN.search(REAL_BANNER)
    assert match is not None
    assert match.group(0) == "https://brave-purple-horse-42.trycloudflare.com"


def test_url_pattern_ignores_other_hosts():
    assert tunnel_module.URL_PATTERN.search("https://example.com/play") is None


def test_start_captures_the_url_and_publishes_it(tmp_path, monkeypatch):
    monkeypatch.delenv("PUBLIC_URL", raising=False)
    stub = make_stub(
        tmp_path,
        'echo "INF |  https://tiny-blue-taco-7.trycloudflare.com  |"\nsleep 30',
    )
    tunnel = Tunnel(port=8000, timeout=10.0, binary=stub)
    try:
        url = tunnel.start()
        assert url == "https://tiny-blue-taco-7.trycloudflare.com"
        assert tunnel.alive
        tunnel.publish()
        assert os.environ["PUBLIC_URL"] == url
    finally:
        tunnel.stop()
    assert not tunnel.alive


def test_missing_binary_is_reported_not_raised():
    tunnel = Tunnel(port=8000, timeout=1.0, binary="definitely-not-installed-xyz")
    assert tunnel.start() is None
    assert "not installed" in (tunnel.error or "")
    assert tunnel.process is None


def test_a_tunnel_that_never_reports_a_url_times_out(tmp_path):
    stub = make_stub(tmp_path, 'echo "INF starting"\nsleep 30')
    tunnel = Tunnel(port=8000, timeout=0.6, binary=stub)
    try:
        assert tunnel.start() is None
        assert "did not report a URL" in (tunnel.error or "")
    finally:
        tunnel.stop()


def test_a_tunnel_that_exits_early_does_not_hang(tmp_path):
    stub = make_stub(tmp_path, 'echo "ERR failed to connect"', exit_code=1)
    tunnel = Tunnel(port=8000, timeout=10.0, binary=stub)
    assert tunnel.start() is None
    assert tunnel.recent_log(), "the failure output should be kept for the operator"


def test_publish_without_a_url_leaves_the_environment_alone(monkeypatch):
    monkeypatch.delenv("PUBLIC_URL", raising=False)
    Tunnel(port=8000).publish()
    assert "PUBLIC_URL" not in os.environ


def test_stop_is_safe_to_call_twice(tmp_path):
    stub = make_stub(tmp_path, 'echo "INF |  https://a-b-c.trycloudflare.com |"\nsleep 30')
    tunnel = Tunnel(port=8000, timeout=10.0, binary=stub)
    tunnel.start()
    tunnel.stop()
    tunnel.stop()
    assert not tunnel.alive


@pytest.mark.parametrize("binary", ["sh", "definitely-not-installed-xyz"])
def test_availability_check(binary):
    assert is_available(binary) is (binary == "sh")
