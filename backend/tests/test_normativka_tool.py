from unittest.mock import AsyncMock, MagicMock

import pytest

from app.normativka.acts import CODICES, resolve_act
from app.pipelines.tools.normativka import StatuteArticleHandler, normativka_tool_specs


def test_resolve_act_by_alias_and_name():
    assert resolve_act("ТК РФ").name == "Трудовой кодекс Российской Федерации"
    assert resolve_act("тк").number == "197-ФЗ"
    # «ТК» is the labour code, never the 1993 customs code.
    assert resolve_act("ТК").name.startswith("Трудовой")
    assert resolve_act("Семейный кодекс").number == "223-ФЗ"
    assert resolve_act("ГК").name.endswith("Часть первая")
    assert resolve_act("НК РФ часть 2").nd == "102067058"


def test_resolve_act_unknown_and_ambiguous():
    assert resolve_act("") is None
    assert resolve_act("закон о полиции") is None
    # Bare «кодекс» prefixes half the table — ambiguous, hence None.
    assert resolve_act("кодекс") is None


def test_codices_table_is_consistent():
    nds = [act.nd for act in CODICES]
    assert len(nds) == len(set(nds)) == 25
    assert all(act.kind == "kodeks" for act in CODICES)


@pytest.fixture
def searcher():
    mock = MagicMock()
    mock.search = AsyncMock(return_value=[])
    mock.resolve = AsyncMock(return_value=None)
    mock.get_article = AsyncMock(return_value=None)
    return mock


@pytest.fixture
def tools(searcher):
    specs = normativka_tool_specs(searcher)
    return {spec.tool.name: spec for spec in specs}


def test_tool_names_and_handlers(tools):
    assert set(tools) == {"search_normativka", "get_statute_article"}
    assert isinstance(tools["get_statute_article"].handler, StatuteArticleHandler)
    assert not tools["search_normativka"].terminal
    assert not tools["get_statute_article"].terminal


@pytest.mark.asyncio
async def test_search_resolves_act_filters(tools, searcher):
    await tools["search_normativka"].tool.ainvoke(
        {"queries": ["расторжение трудового договора"], "acts": ["ТК РФ", "неведомый акт"]}
    )
    kwargs = searcher.search.call_args.kwargs
    assert kwargs["act_nds"] == ["102074279"]


@pytest.mark.asyncio
async def test_search_reports_unrecognized_filters(tools, searcher):
    result = await tools["search_normativka"].tool.ainvoke(
        {"queries": ["неустойка"], "acts": ["неведомый акт"]}
    )
    # No resolvable filter -> searched the whole corpus, and said which
    # filters were dropped instead of silently ignoring them.
    assert searcher.search.call_args.kwargs["act_nds"] is None
    assert "не распознаны" in result


@pytest.mark.asyncio
async def test_get_article_unknown_act_lists_vocabulary(tools):
    result = await tools["get_statute_article"].tool.ainvoke({"act": "закон о тишине", "article": "5"})
    assert "не распознан" in result
    assert "ТК РФ" in result  # the vocabulary is offered right in the reply


@pytest.mark.asyncio
async def test_get_article_found(tools, searcher):
    searcher.resolve = AsyncMock(
        return_value={
            "article_id": "abc",
            "act_name": "Трудовой кодекс Российской Федерации",
            "article_number": "81",
            "article_title": "Расторжение…",
            "article_text": "Текст",
        }
    )
    result = await tools["get_statute_article"].tool.ainvoke({"act": "ТК", "article": "ст. 81"})
    searcher.resolve.assert_awaited_once_with("102074279", "ст. 81")
    assert "ст. 81 — Трудовой кодекс" in result


@pytest.mark.asyncio
async def test_handler_rehydrates_by_reference(searcher):
    searcher.resolve = AsyncMock(return_value={"article_id": "x", "article_text": "Текст", "article_number": "81"})
    handler = StatuteArticleHandler(searcher)
    state = await handler.capture(args={"act": "ТК РФ", "article": "81"}, content="ignored")
    assert state == {"act": "ТК РФ", "article": "81"}
    replayed = await handler.run(args={}, state=state)
    searcher.resolve.assert_awaited_once_with("102074279", "81")
    assert "Текст" in replayed
