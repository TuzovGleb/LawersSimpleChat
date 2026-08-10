"""JSONFormatter must survive whatever exc_info a library puts on the record.

opensearch-py logs failed non-2xx requests with ``exc_info=False`` (a bool), and
``Logger._log`` normalizes only truthy values — so the LogRecord reaches the
formatter with a raw ``False``, which used to crash ``formatException`` and lose
the log line entirely ("--- Logging error ---" spam in prod).
"""
import json
import logging
import sys

from app.utils import JSONFormatter


def _make_record(exc_info) -> logging.LogRecord:
    return logging.LogRecord(
        name="opensearch",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="POST http://localhost:9200/_bulk [status:503 request:0.1s]",
        args=(),
        exc_info=exc_info,
    )


def test_exc_info_false_does_not_crash_and_omits_key():
    record = _make_record(exc_info=False)
    payload = json.loads(JSONFormatter().format(record))
    assert "exc_info" not in payload
    assert payload["message"].startswith("POST http://localhost:9200/_bulk")


def test_exc_info_real_tuple_renders_traceback():
    try:
        raise ValueError("boom")
    except ValueError:
        record = _make_record(exc_info=sys.exc_info())
    payload = json.loads(JSONFormatter().format(record))
    assert "Traceback" in payload["exc_info"]
    assert "ValueError: boom" in payload["exc_info"]


def test_exc_info_bool_true_falls_back_to_current_exception():
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        record = _make_record(exc_info=True)
        payload = json.loads(JSONFormatter().format(record))
    assert "RuntimeError: boom" in payload["exc_info"]
