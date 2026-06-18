"""Shared utilities: structured JSON logging with per-request context.

Every log record is rendered as a single line of JSON so Yandex Cloud Logging
(and any container stdout collector) parses it into ``json_payload`` and lets us
filter by ``chat_id`` / ``request_id`` / ``surface``. The per-request context is
set once by :class:`RequestContextMiddleware` and stamped onto every record by
:class:`RequestContextFilter`, so call sites no longer need to thread
``extra={"session_id": ...}`` by hand.
"""
from __future__ import annotations

import contextvars
import datetime as dt
import json
import logging
import uuid

# Surface tag stamped on every log line from this service. The Next.js BFF and
# the browser emit "bff" / "client", so a single chat can be followed across all
# three tiers in one log group.
SURFACE = "backend"

_chat_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "chat_id", default=None
)
_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


def bind_request_context(*, chat_id: str | None, request_id: str | None):
    """Bind chat/request ids for the current async context; returns reset tokens."""
    return (_chat_id_var.set(chat_id), _request_id_var.set(request_id))


def reset_request_context(tokens) -> None:
    chat_token, request_token = tokens
    _chat_id_var.reset(chat_token)
    _request_id_var.reset(request_token)


def current_chat_id() -> str | None:
    return _chat_id_var.get()


def current_request_id() -> str | None:
    return _request_id_var.get()


class RequestContextFilter(logging.Filter):
    """Stamp surface + chat_id + request_id onto every record from contextvars.

    Explicit ``extra={...}`` values win over the contextvars, and the legacy
    ``session_id`` extra is mirrored to ``chat_id`` for backward compatibility.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "surface", None):
            record.surface = SURFACE
        if not getattr(record, "chat_id", None):
            chat_id = _chat_id_var.get() or getattr(record, "session_id", None)
            if chat_id:
                record.chat_id = chat_id
        if not getattr(record, "request_id", None):
            request_id = _request_id_var.get()
            if request_id:
                record.request_id = request_id
        return True


class JSONFormatter(logging.Formatter):
    """Renders log records as single-line JSON for easy prod ingestion."""

    def __init__(self, *, fmt_keys: dict[str, str] | None = None):
        super().__init__()
        self.fmt_keys = fmt_keys if fmt_keys is not None else {}

    def format(self, record: logging.LogRecord) -> str:
        message = self._prepare_log_dict(record)
        return json.dumps(message, default=str, ensure_ascii=False)

    def _prepare_log_dict(self, record: logging.LogRecord) -> dict:
        always_fields = {
            "message": record.getMessage(),
            "timestamp": dt.datetime.fromtimestamp(
                record.created, tz=dt.timezone.utc
            ).isoformat(),
        }
        if record.exc_info is not None:
            always_fields["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info is not None:
            always_fields["stack_info"] = self.formatStack(record.stack_info)

        message = {
            key: msg_val
            if (msg_val := always_fields.pop(val, None)) is not None
            else getattr(record, val, None)
            for key, val in self.fmt_keys.items()
        }
        message.update(always_fields)

        # Attach any structured extras passed via logger(..., extra={...}) plus
        # the surface/chat_id/request_id stamped by RequestContextFilter.
        standard = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)
        standard.update({"message", "asctime"})
        for key, value in record.__dict__.items():
            if key not in standard and key not in message:
                message[key] = value

        return message


def _header(headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
    for key, value in headers:
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def _chat_id_from_path(path: str) -> str | None:
    """Extract ``<chat_id>`` from ``/chats/<chat_id>/messages``."""
    parts = [p for p in path.split("/") if p]
    if "chats" in parts:
        idx = parts.index("chats")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return None


class RequestContextMiddleware:
    """Pure-ASGI middleware that binds chat_id/request_id for the whole request.

    Pure ASGI (not ``BaseHTTPMiddleware``) on purpose: the contextvars must be
    visible to the endpoint *and* the SSE streaming generator. BaseHTTPMiddleware
    runs the downstream app in a separate task and would lose them. The request
    id is read from ``X-Request-Id`` (the BFF forwards it) or generated, and
    echoed back on the response so the client/BFF can correlate.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers") or []
        request_id = _header(headers, b"x-request-id") or uuid.uuid4().hex
        chat_id = _header(headers, b"x-chat-id") or _chat_id_from_path(
            scope.get("path", "")
        )
        tokens = bind_request_context(chat_id=chat_id, request_id=request_id)

        async def send_with_request_id(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"].append(
                    (b"x-request-id", request_id.encode("latin-1"))
                )
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            reset_request_context(tokens)
