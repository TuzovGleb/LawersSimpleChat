"""Shared utilities: structured JSON log formatter for file logging."""
import datetime as dt
import json
import logging


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

        # Attach any structured extras passed via logger(..., extra={...}).
        standard = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)
        standard.update({"message", "asctime"})
        for key, value in record.__dict__.items():
            if key not in standard and key not in message:
                message[key] = value

        return message
