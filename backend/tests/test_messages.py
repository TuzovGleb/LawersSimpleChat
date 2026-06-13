from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.pipelines.messages import (
    MISSING_TOOL_RESULT,
    messages_to_rows,
    rows_to_messages,
    split_generated,
    text_of,
)
from app.pipelines.tools import court_practice_tool_specs, handlers_of
from app.search.client import OpenSearchConfig
from app.search.search import CourtPracticeSearcher


def _handlers(searcher):
    return handlers_of(court_practice_tool_specs(searcher))


def test_text_of_flattens_list_content():
    assert text_of("plain") == "plain"
    assert text_of([{"type": "text", "text": "Привет"}, {"type": "text", "text": "!"}]) == "Привет!"
    assert text_of(None) == ""


@pytest.mark.asyncio
async def test_messages_to_rows_drops_orphan_trailing_tool_call():
    # Loop ended on an AIMessage(tool_calls) that was never executed (round-limit cutoff).
    generated = [
        AIMessage(content="", tool_calls=[{"name": "x", "args": {}, "id": "c1"}]),
    ]
    rows = await messages_to_rows(generated, {})
    # No answering tool row -> the empty orphan assistant row is omitted entirely.
    assert rows == []


@pytest.mark.asyncio
async def test_rows_to_messages_replays_corrupted_orphan_call_safely():
    # An already-persisted assistant row with tool_calls but no following tool row.
    rows = [
        {"role": "user", "content": "вопрос", "attachedDocumentIds": []},
        {"role": "assistant", "content": "частичный ответ", "tool_calls": [{"id": "c1", "name": "x", "args": {}}]},
        {"role": "user", "content": "ещё вопрос", "attachedDocumentIds": []},
    ]
    messages = await rows_to_messages(rows, {}, {})
    # The dangling tool_call is dropped; no AIMessage carries an unanswered call.
    for m in messages:
        if isinstance(m, AIMessage):
            assert not getattr(m, "tool_calls", None)
    assert any(isinstance(m, AIMessage) and m.content == "частичный ответ" for m in messages)


@pytest.mark.asyncio
async def test_rows_to_messages_missing_handler_uses_placeholder():
    rows = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "gone_tool", "args": {}}]},
        {"role": "tool", "tool_call_id": "c1", "tool_name": "gone_tool", "tool_state": {}},
    ]
    messages = await rows_to_messages(rows, {}, {})  # no handler registered for 'gone_tool'
    tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].content == MISSING_TOOL_RESULT


def test_split_generated_returns_messages_after_last_human():
    messages = [
        SystemMessage(content="sys"),
        HumanMessage(content="вопрос"),
        AIMessage(content="", tool_calls=[{"name": "get_court_decision", "args": {"decision_id": "x"}, "id": "c1"}]),
        ToolMessage(content="...", tool_call_id="c1"),
        AIMessage(content="ответ"),
    ]
    generated = split_generated(messages)
    assert len(generated) == 3
    assert isinstance(generated[0], AIMessage)
    assert isinstance(generated[-1], AIMessage)


@pytest.mark.asyncio
async def test_get_court_decision_result_is_not_stored_only_id():
    searcher = CourtPracticeSearcher(MagicMock(), OpenSearchConfig())
    handlers = _handlers(searcher)

    generated = [
        AIMessage(
            content="",
            tool_calls=[{"name": "get_court_decision", "args": {"decision_id": "dec-9"}, "id": "c1"}],
        ),
        ToolMessage(content="ОГРОМНЫЙ текст акта" * 1000, tool_call_id="c1"),
        AIMessage(content="итоговый ответ"),
    ]
    rows = await messages_to_rows(generated, handlers)

    assert [r["role"] for r in rows] == ["assistant", "tool", "assistant"]
    tool_row = rows[1]
    # Only the id is persisted, never the heavy act text.
    assert tool_row["tool_state"] == {"decision_id": "dec-9"}
    assert "текст акта" not in str(tool_row["tool_state"])
    assert rows[0]["tool_calls"][0]["name"] == "get_court_decision"


@pytest.mark.asyncio
async def test_rows_to_messages_rehydrates_decision_from_opensearch(monkeypatch):
    searcher = CourtPracticeSearcher(MagicMock(), OpenSearchConfig())

    async def fake_get(decision_id):
        return {"decision_id": decision_id, "act_text": "ПОЛНЫЙ ТЕКСТ", "case_number": "2-1/2026"}

    monkeypatch.setattr(searcher, "get_decision", fake_get)
    handlers = _handlers(searcher)

    rows = [
        {"role": "user", "content": "вопрос", "attachedDocumentIds": []},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "name": "get_court_decision", "args": {"decision_id": "dec-9"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "tool_name": "get_court_decision", "tool_state": {"decision_id": "dec-9"}},
        {"role": "assistant", "content": "итог", "tool_calls": None},
    ]
    messages = await rows_to_messages(rows, {}, handlers)

    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], HumanMessage)
    assert isinstance(messages[2], AIMessage)
    assert messages[2].tool_calls[0]["name"] == "get_court_decision"
    tool_msg = messages[3]
    assert isinstance(tool_msg, ToolMessage)
    assert "ПОЛНЫЙ ТЕКСТ" in tool_msg.content  # rehydrated, not from DB
    assert tool_msg.tool_call_id == "c1"
    assert messages[4].content == "итог"


@pytest.mark.asyncio
async def test_inline_search_result_is_stored_and_replayed_verbatim():
    searcher = CourtPracticeSearcher(MagicMock(), OpenSearchConfig())
    handlers = _handlers(searcher)

    snippet_output = "1. id: a1b2\n   Дело: 2-1/2026\n   Фрагмент: ..."
    generated = [
        AIMessage(content="", tool_calls=[{"name": "search_court_practice", "args": {"queries": ["x"]}, "id": "c1"}]),
        ToolMessage(content=snippet_output, tool_call_id="c1"),
        AIMessage(content="ответ"),
    ]
    rows = await messages_to_rows(generated, handlers)
    assert rows[1]["tool_state"] == {"content": snippet_output}

    replay_rows = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "search_court_practice", "args": {"queries": ["x"]}}]},
        {"role": "tool", "tool_call_id": "c1", "tool_name": "search_court_practice", "tool_state": {"content": snippet_output}},
    ]
    messages = await rows_to_messages(replay_rows, {}, handlers)
    assert messages[-1].content == snippet_output
