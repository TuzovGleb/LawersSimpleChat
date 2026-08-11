"""Fetching a court decision: NotFoundError logging and case-number fallback.

Covers the prod incident where the model passed the case number
("78-КГ21-33-К3") instead of the 32-hex decision id: the direct GET 404'd with
a full ERROR traceback and the tool gave no recovery hint.
"""
import logging
from unittest.mock import MagicMock

import pytest
from opensearchpy.exceptions import NotFoundError

from app.pipelines.tools import court_practice_tool_specs
from app.search.client import OpenSearchConfig
from app.search.search import CourtPracticeSearcher

HEX_ID = "ab" * 16  # sha256 hexdigest()[:32] shape
CASE_NUMBER = "78-КГ21-33-К3"


@pytest.fixture
def mock_searcher():
    client = MagicMock()
    config = OpenSearchConfig()
    return CourtPracticeSearcher(client, config)


class _RecordCollector(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def search_log_records():
    # caplog relies on propagation to root, which app/config/logging.yaml turns
    # off for "app.*" — attach a handler to the module logger directly instead.
    module_logger = logging.getLogger("app.search.search")
    collector = _RecordCollector()
    module_logger.addHandler(collector)
    yield collector.records
    module_logger.removeHandler(collector)


def test_get_decision_sync_not_found_warns_without_traceback(mock_searcher, search_log_records):
    mock_searcher._client.get.side_effect = NotFoundError(404, "not_found", {})
    assert mock_searcher.get_decision_sync(CASE_NUMBER) is None
    assert len(search_log_records) == 1
    # Expected miss: WARNING without exc_info, decision_id kept in structured fields.
    assert search_log_records[0].levelno == logging.WARNING
    assert search_log_records[0].exc_info is None
    assert search_log_records[0].decision_id == CASE_NUMBER


def test_get_decision_sync_unexpected_error_still_logs_exception(mock_searcher, search_log_records):
    mock_searcher._client.get.side_effect = RuntimeError("boom")
    assert mock_searcher.get_decision_sync(HEX_ID) is None
    assert len(search_log_records) == 1
    assert search_log_records[0].levelno == logging.ERROR
    assert search_log_records[0].exc_info is not None


def test_get_decision_by_case_number_term_query_and_shape(mock_searcher):
    source = {"decision_id": HEX_ID, "case_number": CASE_NUMBER, "act_text": "Текст"}
    mock_searcher._client.search.return_value = {
        "hits": {"hits": [{"_id": HEX_ID, "_source": source}]}
    }
    assert mock_searcher.get_decision_by_case_number_sync(CASE_NUMBER) == source
    # Exact term query on the case_number keyword field; size 2 so an ambiguous
    # number is detectable.
    body = mock_searcher._client.search.call_args.kwargs["body"]
    assert body["query"] == {"term": {"case_number": CASE_NUMBER}}
    assert body["size"] == 2


def test_get_decision_by_case_number_no_hits_returns_none(mock_searcher):
    mock_searcher._client.search.return_value = {"hits": {"hits": []}}
    assert mock_searcher.get_decision_by_case_number_sync(CASE_NUMBER) is None


def test_get_decision_by_case_number_ambiguous_refuses(mock_searcher, search_log_records):
    # First-instance numbers ("2-123/2021") repeat across courts: two hits mean
    # we cannot know which act the model meant — refuse rather than guess.
    mock_searcher._client.search.return_value = {
        "hits": {
            "hits": [
                {"_id": "a" * 32, "_source": {"decision_id": "a" * 32}},
                {"_id": "b" * 32, "_source": {"decision_id": "b" * 32}},
            ]
        }
    }
    assert mock_searcher.get_decision_by_case_number_sync("2-123/2021") is None
    assert len(search_log_records) == 1
    assert search_log_records[0].levelno == logging.WARNING


@pytest.fixture
def patched_searcher(mock_searcher, monkeypatch):
    """Searcher with async lookups replaced by recorders; tests tune the returns."""
    calls: list[tuple[str, str]] = []
    returns = {"get": None, "case": None}

    async def fake_get(decision_id: str):
        calls.append(("get", decision_id))
        return returns["get"]

    async def fake_by_case(case_number: str):
        calls.append(("case", case_number))
        return returns["case"]

    monkeypatch.setattr(mock_searcher, "get_decision", fake_get)
    monkeypatch.setattr(mock_searcher, "get_decision_by_case_number", fake_by_case)
    return mock_searcher, calls, returns


@pytest.mark.asyncio
async def test_tool_case_number_arg_skips_get_and_uses_fallback(patched_searcher):
    searcher, calls, returns = patched_searcher
    returns["case"] = {"decision_id": HEX_ID, "case_number": CASE_NUMBER, "act_text": "Текст акта"}
    tool = court_practice_tool_specs(searcher)[1].tool
    out = await tool.ainvoke({"decision_id": CASE_NUMBER})
    # Not a 32-hex id -> no doomed GET (and no 404 log), straight to the fallback.
    assert calls == [("case", CASE_NUMBER)]
    assert "Текст акта" in out


@pytest.mark.asyncio
async def test_tool_hex_id_uses_direct_get(patched_searcher):
    searcher, calls, returns = patched_searcher
    returns["get"] = {"decision_id": HEX_ID, "case_number": CASE_NUMBER, "act_text": "Текст акта"}
    tool = court_practice_tool_specs(searcher)[1].tool
    out = await tool.ainvoke({"decision_id": HEX_ID})
    assert calls == [("get", HEX_ID)]
    assert "Текст акта" in out


@pytest.mark.asyncio
async def test_tool_hex_id_miss_falls_back_to_case_number(patched_searcher):
    searcher, calls, returns = patched_searcher
    returns["case"] = {"decision_id": HEX_ID, "case_number": CASE_NUMBER, "act_text": "Текст акта"}
    tool = court_practice_tool_specs(searcher)[1].tool
    out = await tool.ainvoke({"decision_id": HEX_ID})
    assert calls == [("get", HEX_ID), ("case", HEX_ID)]
    assert "Текст акта" in out


@pytest.mark.asyncio
async def test_tool_not_found_message_instructs_to_use_hex_id(patched_searcher):
    searcher, calls, returns = patched_searcher
    tool = court_practice_tool_specs(searcher)[1].tool
    out = await tool.ainvoke({"decision_id": CASE_NUMBER})
    assert "не найдено" in out
    # The message must steer the model to the "id:" line, away from the case number.
    assert "32-символьный" in out
    assert "search_court_practice" in out
    assert "не номер дела" in out


@pytest.mark.asyncio
async def test_replay_handler_falls_back_to_case_number(patched_searcher):
    # The replay path stores whatever the model passed as decision_id — it needs
    # the same fallback, or a fallback-served turn breaks on rehydration.
    searcher, calls, returns = patched_searcher
    returns["case"] = {"decision_id": HEX_ID, "case_number": CASE_NUMBER, "act_text": "Текст акта"}
    handler = court_practice_tool_specs(searcher)[1].handler
    out = await handler.run(args={}, state={"decision_id": CASE_NUMBER})
    assert calls == [("case", CASE_NUMBER)]
    assert "Текст акта" in out


@pytest.mark.asyncio
async def test_capture_pins_resolved_id_for_case_number(patched_searcher):
    # A fallback-served turn must replay by the RESOLVED hex id: replaying the
    # raw case number is nondeterministic once a colliding number gets indexed.
    searcher, calls, returns = patched_searcher
    returns["case"] = {"decision_id": HEX_ID, "case_number": CASE_NUMBER, "act_text": "Текст акта"}
    handler = court_practice_tool_specs(searcher)[1].handler
    state = await handler.capture(args={"decision_id": f" {CASE_NUMBER} "}, content="...")
    assert state == {"decision_id": HEX_ID}
    assert calls == [("case", CASE_NUMBER)]


@pytest.mark.asyncio
async def test_capture_keeps_hex_id_without_lookup(patched_searcher):
    searcher, calls, returns = patched_searcher
    handler = court_practice_tool_specs(searcher)[1].handler
    state = await handler.capture(args={"decision_id": HEX_ID}, content="...")
    assert state == {"decision_id": HEX_ID}
    assert calls == []


@pytest.mark.asyncio
async def test_capture_unresolvable_keeps_stripped_raw_value(patched_searcher):
    searcher, calls, returns = patched_searcher
    handler = court_practice_tool_specs(searcher)[1].handler
    state = await handler.capture(args={"decision_id": f"{CASE_NUMBER} "}, content="...")
    assert state == {"decision_id": CASE_NUMBER}
