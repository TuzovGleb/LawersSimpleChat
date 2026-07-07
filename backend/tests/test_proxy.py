"""Tests for the rotating-proxy egress transport (app.rag_core.proxy)."""
import asyncio
import base64

import httpx
import pytest

from app.rag_core import proxy as proxymod
from app.rag_core.proxy import (
    RotatingProxyTransport,
    _load_proxy_urls,
    get_proxy_client,
)

_PROXY_ENVS = ("PROXY_ENABLED", "PROXY_LIST", "PROXY_LIST_B64", "PROXY_FILE")


@pytest.fixture(autouse=True)
def _clean_proxy_state(monkeypatch):
    """Every test starts with the cached client cleared and no proxy env vars."""
    for var in _PROXY_ENVS:
        monkeypatch.delenv(var, raising=False)
    proxymod._client = None
    yield
    proxymod._client = None


def _member(label, calls, *, dead=False):
    """A ``(label, transport)`` pair whose handler records the call and either
    returns 200 or simulates a connection failure."""

    def handler(request):
        calls.append(label)
        if dead:
            raise httpx.ConnectError("dead proxy", request=request)
        return httpx.Response(200)

    return (label, httpx.MockTransport(handler))


def _request():
    return httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")


# --- rotation ----------------------------------------------------------------

async def test_round_robin_cycles_all_proxies():
    calls: list[str] = []
    transport = RotatingProxyTransport(
        [_member("A", calls), _member("B", calls), _member("C", calls)]
    )
    for _ in range(6):
        resp = await transport.handle_async_request(_request())
        assert resp.status_code == 200
    assert calls == ["A", "B", "C", "A", "B", "C"]


async def test_rotates_to_next_proxy_on_connection_failure():
    """A dead proxy mid-pool must not fail the request: rotate to a live one."""
    calls: list[str] = []
    transport = RotatingProxyTransport(
        [_member("dead", calls, dead=True), _member("live", calls)]
    )
    resp = await transport.handle_async_request(_request())
    assert resp.status_code == 200
    assert calls == ["dead", "live"]


async def test_all_proxies_dead_raises_connect_error():
    calls: list[str] = []
    transport = RotatingProxyTransport(
        [_member("d1", calls, dead=True), _member("d2", calls, dead=True)]
    )
    with pytest.raises(httpx.ConnectError):
        await transport.handle_async_request(_request())
    assert calls == ["d1", "d2"]  # tried every proxy once


# --- circuit breaker ---------------------------------------------------------

async def test_circuit_opens_and_skips_dead_proxy():
    calls: list[str] = []
    transport = RotatingProxyTransport(
        [_member("dead", calls, dead=True), _member("live", calls)],
        fail_threshold=2,
        cooldown_s=100.0,
    )
    # Two requests: "dead" fails twice (hits threshold) but each request still
    # succeeds via "live".
    for _ in range(2):
        assert (await transport.handle_async_request(_request())).status_code == 200
    assert calls.count("dead") == 2

    # Circuit for "dead" is now open: further requests skip it entirely.
    for _ in range(3):
        assert (await transport.handle_async_request(_request())).status_code == 200
    assert calls.count("dead") == 2  # unchanged — never attempted again


async def test_circuit_half_opens_after_cooldown():
    calls: list[str] = []
    transport = RotatingProxyTransport(
        [_member("dead", calls, dead=True), _member("live", calls)],
        fail_threshold=1,
        cooldown_s=0.05,
    )
    await transport.handle_async_request(_request())  # opens "dead" circuit
    assert calls.count("dead") == 1
    await transport.handle_async_request(_request())  # cooldown holds -> skipped
    assert calls.count("dead") == 1
    await asyncio.sleep(0.06)
    await transport.handle_async_request(_request())  # cooldown elapsed -> retried
    assert calls.count("dead") == 2


async def test_http_response_passes_through_without_rotation():
    """A real HTTP status (even 5xx from OpenRouter) is returned as-is, not
    treated as a proxy failure — the SDK/fallback chain handles it."""
    calls: list[str] = []

    def handler(request):
        calls.append("A")
        return httpx.Response(503)

    transport = RotatingProxyTransport([("A", httpx.MockTransport(handler))])
    resp = await transport.handle_async_request(_request())
    assert resp.status_code == 503
    assert calls == ["A"]  # no rotation on an HTTP error


def test_empty_pool_raises():
    with pytest.raises(RuntimeError, match="empty proxy pool"):
        RotatingProxyTransport([])


# --- pool loading / parsing --------------------------------------------------

def test_parse_webshare_format(monkeypatch):
    monkeypatch.setenv("PROXY_LIST", "1.2.3.4:5500:user:pass\n6.7.8.9:6376:u2:p2")
    assert _load_proxy_urls() == [
        "http://user:pass@1.2.3.4:5500",
        "http://u2:p2@6.7.8.9:6376",
    ]


def test_parse_mixed_separators_and_skips_malformed(monkeypatch):
    monkeypatch.setenv("PROXY_LIST", "1.1.1.1:80:u:p ; badentry , 2.2.2.2:81:u2:p2")
    assert _load_proxy_urls() == [
        "http://u:p@1.1.1.1:80",
        "http://u2:p2@2.2.2.2:81",
    ]


def test_base64_source_is_preferred(monkeypatch):
    raw = "9.9.9.9:1080:usr:pwd"
    monkeypatch.setenv("PROXY_LIST_B64", base64.b64encode(raw.encode()).decode())
    monkeypatch.setenv("PROXY_LIST", "0.0.0.0:1:ignored:ignored")
    assert _load_proxy_urls() == ["http://usr:pwd@9.9.9.9:1080"]


# --- get_proxy_client toggle -------------------------------------------------

def test_disabled_returns_none():
    assert get_proxy_client() is None


def test_enabled_but_empty_pool_fails_loud(monkeypatch):
    monkeypatch.setenv("PROXY_ENABLED", "true")
    with pytest.raises(RuntimeError, match="no proxies"):
        get_proxy_client()


def test_enabled_builds_cached_singleton(monkeypatch):
    monkeypatch.setenv("PROXY_ENABLED", "true")
    monkeypatch.setenv("PROXY_LIST", "1.2.3.4:5500:user:pass")
    client = get_proxy_client()
    assert isinstance(client, httpx.AsyncClient)
    assert get_proxy_client() is client  # cached
