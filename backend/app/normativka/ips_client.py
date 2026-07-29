"""HTTP client for pravo.gov.ru ИПС «Законодательство России».

Every wire-format quirk verified live (2026-07) lives here so callers never
see them:

* the portal is windows-1251 end to end: query VALUES must be cp1251-encoded
  BEFORE percent-encoding, responses must be decoded from cp1251;
* the search form validates the FULL field set — omitting an empty ``aN``
  field yields «Неверные параметры запроса», while an empty ``intelsearch=``
  param on attribute searches yields an empty HTTP 204;
* the server intermittently stalls mid-response (25-40s hangs observed on the
  same URLs that normally answer in ~1s) — requests are retried with a fresh
  connection;
* responses are cached server-side PER CLIENT IP, and concurrent requests
  from one IP have been observed to receive each other's cached pages
  (cross-talk). Requests therefore go strictly sequentially through one
  client, and callers can pass ``echo`` to verify the response actually
  answers their query.
"""
import logging
import time
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "http://pravo.gov.ru/proxy/ips/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)

# The complete attribute-form field set. The server rejects requests that omit
# any of these keys, even empty (verified: partial sets error out). Order is
# preserved as the browser sends it.
_FORM_FIELDS: tuple[tuple[str, str], ...] = (
    ("bpas", "cd00000"),
    ("a3", ""), ("a3type", "1"), ("a3value", ""),
    ("a6", ""), ("a6type", "1"), ("a6value", ""),
    ("a15", ""), ("a15type", "1"), ("a15value", ""),
    ("a7type", "1"), ("a7from", ""), ("a7to", ""), ("a7date", ""),
    ("a8", ""), ("a8type", "1"),
    ("a1", ""), ("a0", ""),
    ("a16", ""), ("a16type", "1"), ("a16value", ""),
    ("a17", ""), ("a17type", "1"), ("a17value", ""),
    ("a4", ""), ("a4type", "1"), ("a4value", ""),
    ("a23", ""), ("a23type", "1"), ("a23value", ""),
    ("textpres", "yes"),
)


class IpsError(RuntimeError):
    """A request the ИПС could not serve after retries."""


def _encode_value(value: str) -> str:
    """Percent-encode a query value in windows-1251 (the portal's charset)."""
    return quote(value.encode("cp1251"), safe="")


def build_query(view: str, overrides: dict[str, str], *, extra: dict[str, str] | None = None) -> str:
    """Build the full ИПС query string for a view (``list_itself``, ``docbody``, …).

    ``overrides`` replace values of the standard form fields; ``extra`` appends
    params outside the form (``sort``, ``start``, ``intelsearch``). NB: an
    EMPTY extra value is dropped — the server treats e.g. a present-but-empty
    ``intelsearch=`` as a malformed request (HTTP 204).
    """
    parts = [f"{view}="]
    for key, default in _FORM_FIELDS:
        value = overrides.get(key, default)
        parts.append(f"{key}={_encode_value(value)}" if value else f"{key}=")
    for key, value in (extra or {}).items():
        if value:
            parts.append(f"{key}={_encode_value(value)}")
    return "&".join(parts)


class IpsClient:
    """Serial, retrying HTTP client for the ИПС. Not thread-safe by design."""

    def __init__(
        self,
        *,
        base_url: str = BASE_URL,
        timeout: float = 90.0,
        retries: int = 3,
        pause: float = 1.0,
    ):
        self._base_url = base_url
        self._timeout = timeout
        self._retries = retries
        self._pause = pause
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "IpsClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def get_raw(self, query: str, *, min_bytes: int = 0, timeout: float | None = None) -> bytes:
        """GET ``?{query}`` with retries; returns the raw (cp1251) body.

        ``min_bytes`` guards against the portal's known failure shapes: stub
        pages (~6KB for an invalid rdk) and truncated streams from mid-response
        stalls both come back as HTTP 200, so status alone proves nothing.
        ``timeout`` overrides the client default per request (the MHT export
        of the biggest acts takes minutes server-side).
        """
        url = f"{self._base_url}?{query}"
        last_error: Exception | None = None
        for attempt in range(1, self._retries + 1):
            try:
                response = self._client.get(url, timeout=timeout or self._timeout)
                if response.status_code == 200 and len(response.content) >= min_bytes:
                    return response.content
                last_error = IpsError(
                    f"HTTP {response.status_code}, {len(response.content)}B (< {min_bytes}B min)"
                )
            except httpx.HTTPError as exc:
                last_error = exc
            logger.warning(
                "ИПС request failed, retrying",
                extra={"attempt": attempt, "url": url[:200], "error": str(last_error)},
            )
            time.sleep(self._pause * attempt)
        raise IpsError(f"ИПС не ответил после {self._retries} попыток: {last_error}") from last_error

    def get_text(self, query: str, *, min_bytes: int = 0, echo: str | None = None) -> str:
        """GET and decode from cp1251. ``echo``: substring that MUST be present
        in the decoded page — the guard against the per-IP cache returning a
        response to someone else's query (observed live)."""
        body = self.get_raw(query, min_bytes=min_bytes)
        text = body.decode("cp1251", errors="replace")
        if echo and echo not in text:
            raise IpsError(f"Ответ ИПС не содержит ожидаемый маркер {echo!r} — cross-talk или чужая страница")
        time.sleep(self._pause)
        return text
