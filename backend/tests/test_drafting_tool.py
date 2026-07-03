"""Behavioural tests for the draft_document tool: truncation guard, classifier
retry/degradation, actual-request marker and full-text replay for iterative
edits. LLMs are stubbed — no network."""
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.pipelines.tools.drafting import (
    DraftHandler,
    _blocks_text,
    _serialize_history,
    drafting_tool_specs,
)
from app.services.docx_drafting import ClassifiedDoc


class StubLLM:
    """Async LLM stub: returns a fixed AIMessage."""

    def __init__(self, content: str, finish_reason: str = "stop"):
        self._message = AIMessage(
            content=content, response_metadata={"finish_reason": finish_reason}
        )

    async def ainvoke(self, _input):
        return self._message


DRAFT_TEXT = "В суд\nИстец: Иванов\n\nВОЗРАЖЕНИЯ\n\nТекст позиции."


def _tool(drafting_llm, segmenter=None):
    return drafting_tool_specs(drafting_llm, segmenter or StubLLM(""))[0].tool


def _state():
    return {"messages": [HumanMessage(content="Составь возражения")]}


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_reason", ["length", "lengthlength"])
async def test_truncated_draft_fails_instead_of_shipping_partial(finish_reason):
    # "lengthlength" — реальная форма из прода: при streaming=True OpenRouter
    # шлёт finish_reason в двух чанках, langchain склеивает строки конкатенацией.
    tool = _tool(StubLLM(DRAFT_TEXT, finish_reason=finish_reason))
    result = json.loads(await tool.coroutine(state=_state()))
    assert result["status"] == "failed"
    assert result["blocks"] == []


@pytest.mark.asyncio
async def test_missing_finish_reason_still_ships(monkeypatch):
    async def classify(_llm, units):
        return ClassifiedDoc(
            file_name="Документ", lines=[{"id": u["id"], "type": "body"} for u in units]
        )

    monkeypatch.setattr("app.pipelines.tools.drafting.classify_lines", classify)
    stub = StubLLM(DRAFT_TEXT)
    stub._message = AIMessage(content=DRAFT_TEXT, response_metadata={})
    result = json.loads(await _tool(stub).coroutine(state=_state()))
    assert result["status"] == "ready"


@pytest.mark.asyncio
async def test_classifier_succeeds_on_second_attempt(monkeypatch):
    calls = {"n": 0}

    async def flaky(_llm, units):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return ClassifiedDoc(
            file_name="Возражения",
            lines=[{"id": u["id"], "type": "body"} for u in units],
        )

    monkeypatch.setattr("app.pipelines.tools.drafting.classify_lines", flaky)
    result = json.loads(await _tool(StubLLM(DRAFT_TEXT)).coroutine(state=_state()))
    assert calls["n"] == 2
    assert result["status"] == "ready"
    assert result["degraded"] is False
    assert result["file_name"] == "Возражения"


@pytest.mark.asyncio
async def test_classifier_failure_degrades_but_stays_ready(monkeypatch):
    calls = {"n": 0}

    async def boom(_llm, _units):
        calls["n"] += 1
        raise RuntimeError("classifier down")

    monkeypatch.setattr("app.pipelines.tools.drafting.classify_lines", boom)
    tool = _tool(StubLLM(DRAFT_TEXT))
    result = json.loads(await tool.coroutine(state=_state()))
    assert calls["n"] == 2  # one retry
    assert result["status"] == "ready"
    assert result["degraded"] is True
    assert all(b["type"] in ("body", "spacer") for b in result["blocks"])


@pytest.mark.asyncio
async def test_successful_classification_marks_types(monkeypatch):
    async def classify(_llm, units):
        types = ["header", "header", "title", "body"]
        return ClassifiedDoc(
            file_name="Возражения",
            lines=[
                {"id": u["id"], "type": types[i]} for i, u in enumerate(units)
            ],
        )

    monkeypatch.setattr("app.pipelines.tools.drafting.classify_lines", classify)
    tool = _tool(StubLLM(DRAFT_TEXT))
    result = json.loads(await tool.coroutine(state=_state()))
    assert result["status"] == "ready"
    assert result["degraded"] is False
    assert result["file_name"] == "Возражения"
    assert [b["type"] for b in result["blocks"] if b["type"] != "spacer"] == [
        "header", "header", "title", "body",
    ]


def test_serialize_history_marks_last_lawyer_message():
    payload = json.loads(
        _serialize_history(
            [
                HumanMessage(content="Первый вопрос"),
                AIMessage(content="Ответ"),
                HumanMessage(content="Составь документ"),
            ]
        )
    )
    roles = [item["role"] for item in payload]
    assert roles[0] == "Юрист"
    assert roles[1] == "Ассистент"
    assert "АКТУАЛЬНЫЙ ЗАПРОС" in roles[2]
    assert sum("АКТУАЛЬНЫЙ ЗАПРОС" in r for r in roles) == 1


def test_blocks_text_flattens_paragraphs_and_tables():
    text = _blocks_text(
        [
            {"type": "header", "text": "В суд"},
            {"type": "spacer", "text": ""},
            {"type": "table", "rows": [{"cells": ["Период", "Сумма"]}, {"cells": ["2025", "100"]}]},
            {"type": "body", "text": "Абзац."},
        ]
    )
    assert text.splitlines() == ["В суд", "", "Период | Сумма", "2025 | 100", "Абзац."]


@pytest.mark.asyncio
async def test_handler_replays_full_text_for_iterative_edits():
    handler = DraftHandler()
    state = await handler.capture(
        args={},
        content=json.dumps(
            {
                "status": "ready",
                "file_name": "Возражения",
                "blocks": [{"type": "title", "text": "ВОЗРАЖЕНИЯ"}, {"type": "body", "text": "Довод."}],
                "degraded": False,
            },
            ensure_ascii=False,
        ),
    )
    assert state["degraded"] is False
    replay = await handler.run(args={}, state=state)
    assert "подготовлен" in replay
    assert "ВОЗРАЖЕНИЯ" in replay and "Довод." in replay


@pytest.mark.asyncio
async def test_handler_failed_state_has_no_text():
    handler = DraftHandler()
    replay = await handler.run(
        args={}, state={"status": "failed", "file_name": "Документ", "blocks": []}
    )
    assert "Не удалось" in replay
