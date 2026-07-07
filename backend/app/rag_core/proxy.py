"""Rotating-proxy egress for OpenRouter.

Our Yandex Cloud egress IP is blocked by OpenRouter's edge ("Access denied by
security policy", HTTP 403 across *every* upstream provider — the block is at
the CDN, before model routing). Routing outbound requests through a pool of
non-RU proxies bypasses it.

Design (see the chat thread that introduced this file):

* A single shared :class:`httpx.AsyncClient` is injected into every
  ``ChatOpenAI`` via ``http_async_client`` (see ``llm.build_chat_llm`` — the one
  construction site all chat/OCR/segmenter/drafting models funnel through). One
  client ⇒ one connection pool ⇒ one shared circuit-breaker state.
* The client's transport is :class:`RotatingProxyTransport`, which picks a proxy
  **per request** (round-robin) and delegates to a per-proxy
  ``AsyncHTTPTransport``. Per-request selection means a streaming response stays
  pinned to one proxy for its whole life — we never switch mid-stream.
* A per-proxy circuit breaker takes a proxy out of rotation after
  ``fail_threshold`` consecutive *connection* failures, for ``cooldown_s``.
  Within a single request we rotate onto the next healthy proxy on a connection
  failure (up to ``max_tries``); a real HTTP response — including a 5xx from
  OpenRouter itself — is passed straight up to the caller, where the openai-SDK
  retry and the ``nodes._invoke_with_fallback`` model chain already handle it.

Fail-loud, no silent degradation (cf. the "no arbitrary limits" rule): if
proxying is enabled but the pool is empty, or every proxy's circuit is open,
we raise rather than quietly falling back to a direct connection (which would
just hit the same 403 and mask the real problem).

The pool source is intentionally swappable — today it is env/secret
(``_load_proxy_urls``); a Lockbox hot-reload or a DB-backed inventory can drop
in behind the same function without touching the transport.
"""
from __future__ import annotations

import base64
import binascii
import gzip
import itertools
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

# Circuit-breaker + rotation defaults. Overridable per-instance (tests inject
# small values); env-tunable in production without a code change.
_FAIL_THRESHOLD = int(os.getenv("PROXY_FAIL_THRESHOLD", "3"))
_COOLDOWN_S = float(os.getenv("PROXY_COOLDOWN_S", "60"))
_MAX_TRIES = int(os.getenv("PROXY_MAX_TRIES", "4"))

# httpx timeouts for proxied calls. ``read`` matches the 300s ChatOpenAI ceiling
# (heavy legal queries stream long); ``connect`` is tight so a dead proxy fails
# fast and we rotate instead of hanging.
_TIMEOUT = httpx.Timeout(connect=15.0, read=300.0, write=60.0, pool=15.0)
_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20)


@dataclass
class _Proxy:
    label: str  # host:port only — credentials never reach a log line
    transport: httpx.AsyncBaseTransport
    fails: int = 0  # consecutive connection failures
    open_until: float = 0.0  # circuit-open deadline (time.monotonic)


class RotatingProxyTransport(httpx.AsyncBaseTransport):
    """Per-request round-robin over a pool of proxied transports.

    ``members`` is a list of ``(label, transport)`` pairs so tests can inject
    ``httpx.MockTransport`` instances; production builds them from proxy URLs.
    """

    def __init__(
        self,
        members: list[tuple[str, httpx.AsyncBaseTransport]],
        *,
        fail_threshold: int = _FAIL_THRESHOLD,
        cooldown_s: float = _COOLDOWN_S,
        max_tries: int = _MAX_TRIES,
    ):
        if not members:
            raise RuntimeError("RotatingProxyTransport: empty proxy pool")
        self._pool = [_Proxy(label=label, transport=t) for label, t in members]
        self._cycle = itertools.cycle(range(len(self._pool)))
        self._fail_threshold = fail_threshold
        self._cooldown_s = cooldown_s
        # Never try more proxies than exist; bound by the caller's cap otherwise.
        self._max_tries = min(max_tries, len(self._pool))

    def _next_healthy(self, now: float) -> _Proxy | None:
        """Advance the round-robin cursor to the next proxy whose circuit is
        closed, or return ``None`` if every circuit is open."""
        for _ in range(len(self._pool)):
            p = self._pool[next(self._cycle)]
            if p.open_until <= now:
                return p
        return None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        last_exc: Exception | None = None
        for _ in range(self._max_tries):
            now = time.monotonic()
            proxy = self._next_healthy(now)
            if proxy is None:
                break  # all circuits open — fail loud below
            try:
                response = await proxy.transport.handle_async_request(request)
            except httpx.TransportError as exc:
                # Failed to reach OpenRouter *through this proxy* (connect
                # refused/timeout, proxy 407/5xx at the tunnel). Blame the proxy,
                # rotate. The openai request body is buffered bytes, so replaying
                # it on the next proxy is safe (no half-consumed stream yet).
                last_exc = exc
                proxy.fails += 1
                if proxy.fails >= self._fail_threshold and proxy.open_until <= now:
                    proxy.open_until = now + self._cooldown_s
                    logger.warning(
                        "Proxy circuit opened",
                        extra={
                            "proxy": proxy.label,
                            "fails": proxy.fails,
                            "cooldown_s": self._cooldown_s,
                        },
                    )
                continue
            proxy.fails = 0  # connected fine — reset the breaker
            return response

        raise httpx.ConnectError(
            f"All {len(self._pool)} proxies unavailable (circuits open or "
            f"connection failures); last error: {last_exc}",
            request=request,
        )

    async def aclose(self) -> None:
        for proxy in self._pool:
            await proxy.transport.aclose()


# --- pool source (swappable: env/secret today, Lockbox/DB later) -------------

def _proxy_enabled() -> bool:
    return os.getenv("PROXY_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def _load_raw_pool() -> str:
    """Raw proxy list text from the first configured source.

    ``PROXY_LIST_B64`` (base64 of the Webshare export, optionally gzip'd first)
    is preferred because the deploy pipeline joins env vars with commas into
    ``yc ... --environment`` — base64 has no commas or newlines to break that
    join. A YC container env var is capped at 4096 bytes and plain base64 of
    ~100 proxies is ~5.5KB, so the value is gzip'd first (~1.3KB); we detect the
    gzip magic and inflate transparently, so a plain (un-gzipped) base64 value
    still works. ``PROXY_LIST`` (plain) and ``PROXY_FILE`` (a mounted path) are
    conveniences for local/dev.
    """
    b64 = os.getenv("PROXY_LIST_B64")
    if b64:
        b64 = re.sub(r"\s+", "", b64)  # drop any line-wrapping whitespace
        b64 += "=" * (-len(b64) % 4)  # tolerate padding stripped to dodge '=' issues
        try:
            data = base64.b64decode(b64)
        except (binascii.Error, ValueError) as exc:
            raise RuntimeError(f"PROXY_LIST_B64 is not valid base64: {exc}") from exc
        if data[:2] == b"\x1f\x8b":  # gzip magic — inflate to fit the 4096B cap
            data = gzip.decompress(data)
        return data.decode("utf-8")
    raw = os.getenv("PROXY_LIST")
    if raw:
        return raw
    path = os.getenv("PROXY_FILE")
    if path:
        return Path(path).read_text(encoding="utf-8")
    return ""


def _load_proxy_urls() -> list[str]:
    """Parse ``host:port:user:pass`` entries (Webshare export format) into
    ``http://user:pass@host:port`` URLs. Entries may be separated by newlines,
    commas, semicolons or whitespace. Malformed entries are skipped with a log.
    """
    urls: list[str] = []
    for token in re.split(r"[\s;,]+", _load_raw_pool().strip()):
        if not token:
            continue
        parts = token.split(":")
        if len(parts) != 4:
            logger.warning("Skipping malformed proxy entry", extra={"field_count": len(parts)})
            continue
        host, port, user, password = parts
        # Percent-encode credentials so a special char (@ : / #) in user/pass
        # can't corrupt the proxy URL httpx parses.
        user = quote(user, safe="")
        password = quote(password, safe="")
        urls.append(f"http://{user}:{password}@{host}:{port}")
    return urls


_client: httpx.AsyncClient | None = None


def get_proxy_client() -> httpx.AsyncClient | None:
    """Shared async client that routes OpenRouter egress through the rotating
    proxy pool, or ``None`` when proxying is disabled (local dev / an egress
    that isn't blocked) so callers connect directly.

    Built once and cached; every ``ChatOpenAI`` shares it.
    """
    global _client
    if not _proxy_enabled():
        return None
    if _client is None:
        urls = _load_proxy_urls()
        if not urls:
            raise RuntimeError(
                "PROXY_ENABLED is set but no proxies were found in "
                "PROXY_LIST_B64 / PROXY_LIST / PROXY_FILE"
            )
        members = [
            (url.rsplit("@", 1)[-1], httpx.AsyncHTTPTransport(proxy=url, retries=0))
            for url in urls
        ]
        logger.info("Proxy egress enabled", extra={"proxy_count": len(urls)})
        _client = httpx.AsyncClient(
            transport=RotatingProxyTransport(members),
            timeout=_TIMEOUT,
            limits=_LIMITS,
        )
    return _client


async def aclose_proxy_client() -> None:
    """Close the shared client on shutdown (best-effort). Idempotent."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
