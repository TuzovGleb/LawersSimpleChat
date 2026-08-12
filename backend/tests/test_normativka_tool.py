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
    # Index-based act resolution: ФЗ не помещаются в таблицу, их находит индекс.
    mock.resolve_act = AsyncMock(return_value=[])
    mock.act_card = AsyncMock(return_value=None)
    return mock


@pytest.fixture
def tools(searcher):
    specs = normativka_tool_specs(searcher)
    return {spec.tool.name: spec for spec in specs}


def test_tool_names_and_handlers(tools):
    assert set(tools) == {"search_normativka", "get_act_info", "get_statute_article"}
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
    # Кодекс разрешён по таблице — в индекс за ним не ходили; за неизвестным — ходили.
    searcher.resolve_act.assert_awaited_once_with("неведомый акт")


@pytest.mark.asyncio
async def test_search_resolves_federal_law_via_index(tools, searcher):
    searcher.resolve_act = AsyncMock(
        return_value=[{"act_nd": "555000111", "act_name": "О контрактной системе…", "act_number": "44-ФЗ"}]
    )
    await tools["search_normativka"].tool.ainvoke({"queries": ["закупка"], "acts": ["44-ФЗ"]})
    assert searcher.search.call_args.kwargs["act_nds"] == ["555000111"]


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
    # Ни таблица, ни индекс акт не знают — ответ подсказывает, как назвать.
    result = await tools["get_statute_article"].tool.ainvoke({"act": "закон о тишине", "article": "5"})
    assert "в корпусе не найден" in result
    assert "44-ФЗ" in result and "ТК РФ" in result


@pytest.mark.asyncio
async def test_get_article_by_law_number_via_index(tools, searcher):
    searcher.resolve_act = AsyncMock(
        return_value=[
            {"act_nd": "555000111", "act_name": "О персональных данных", "act_number": "152-ФЗ"}
        ]
    )
    searcher.resolve = AsyncMock(
        return_value={
            "article_id": "z",
            "act_name": "О персональных данных",
            "act_number": "152-ФЗ",
            "article_number": "9",
            "article_title": "Согласие субъекта",
            "article_text": "Текст",
        }
    )
    result = await tools["get_statute_article"].tool.ainvoke({"act": "152-ФЗ", "article": "9"})
    searcher.resolve.assert_awaited_once_with("555000111", "9")
    assert "ст. 9 — О персональных данных (152-ФЗ)" in result


@pytest.mark.asyncio
async def test_get_article_offers_alternatives_when_ambiguous(tools, searcher):
    searcher.resolve_act = AsyncMock(
        return_value=[
            {"act_nd": "1", "act_name": "О связи", "act_number": "126-ФЗ"},
            {"act_nd": "2", "act_name": "О почтовой связи", "act_number": "176-ФЗ"},
        ]
    )
    searcher.resolve = AsyncMock(return_value=None)  # статьи нет в выбранном акте
    result = await tools["get_statute_article"].tool.ainvoke({"act": "закон о связи", "article": "99"})
    assert "не найдена" in result
    assert "176-ФЗ" in result  # альтернатива предложена, а не проглочена


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


@pytest.mark.asyncio
async def test_bare_number_without_curated_act_asks_to_disambiguate(tools, searcher):
    # Номера ФЗ повторяются по годам. Если ни один из однофамильцев не из
    # курируемой таблицы, выбирать за юриста нельзя — показываем варианты.
    searcher.resolve_act = AsyncMock(
        return_value=[
            {"act_nd": "1", "act_name": "Об упразднении районных судов Самарской области",
             "act_number": "77-ФЗ", "act_date": "2013-02-04", "articles": 3},
            {"act_nd": "2", "act_name": "О внутренних морских водах",
             "act_number": "77-ФЗ", "act_date": "1998-07-31", "articles": 48},
        ]
    )
    result = await tools["get_statute_article"].tool.ainvoke({"act": "77-ФЗ", "article": "5"})
    searcher.resolve.assert_not_awaited()  # не угадываем
    assert "несколько актов" in result
    assert "1998" in result and "2013" in result


@pytest.mark.asyncio
async def test_curated_act_wins_its_number_without_asking(tools, searcher):
    # «152-ФЗ» юристы называют закон о персональных данных, хотя тот же номер
    # носят «Об ипотечных ценных бумагах» (2003) — и по релевантности названия
    # выигрывали именно они. Курируемые аббревиатуры и решают этот спор.
    searcher.resolve_act = AsyncMock(
        return_value=[
            {"act_nd": "ipo", "act_name": "Об ипотечных ценных бумагах",
             "act_number": "152-ФЗ", "act_date": "2003-11-11", "articles": 46},
            {"act_nd": "pd", "act_name": "О персональных данных",
             "act_number": "152-ФЗ", "act_date": "2006-07-27", "articles": 29},
        ]
    )
    searcher.resolve = AsyncMock(return_value={
        "article_id": "x", "act_name": "О персональных данных", "act_number": "152-ФЗ",
        "article_number": "9", "article_title": "Согласие субъекта", "article_text": "Текст"})
    result = await tools["get_statute_article"].tool.ainvoke({"act": "152-ФЗ", "article": "9"})
    searcher.resolve.assert_awaited_once_with("pd", "9")
    assert "О персональных данных" in result
    assert "несколько актов" not in result


@pytest.mark.asyncio
async def test_search_by_bare_number_filters_all_namesakes(tools, searcher):
    searcher.resolve_act = AsyncMock(
        return_value=[
            {"act_nd": "1", "act_name": "Об ООО", "act_number": "14-ФЗ"},
            {"act_nd": "2", "act_name": "Об упразднении судов", "act_number": "14-ФЗ"},
        ]
    )
    await tools["search_normativka"].tool.ainvoke({"queries": ["выход участника"], "acts": ["14-ФЗ"]})
    # По номеру фильтруем по всем одноимённым — лучше, чем молча потерять нужный.
    assert searcher.search.call_args.kwargs["act_nds"] == ["1", "2"]


@pytest.mark.asyncio
async def test_named_act_filter_takes_only_best_match(tools, searcher):
    searcher.resolve_act = AsyncMock(
        return_value=[
            {"act_nd": "1", "act_name": "О защите прав потребителей", "act_number": "2300-I"},
            {"act_nd": "2", "act_name": "О защите конкуренции", "act_number": "135-ФЗ"},
        ]
    )
    await tools["search_normativka"].tool.ainvoke(
        {"queries": ["возврат товара"], "acts": ["О защите прав потребителей"]}
    )
    assert searcher.search.call_args.kwargs["act_nds"] == ["1"]


@pytest.mark.asyncio
async def test_act_card_answers_request_for_the_whole_act(tools, searcher):
    # «Приведи НК часть первую» — ни поиск (сниппеты), ни статья по номеру не
    # отвечают на вопрос про акт целиком. Раньше модель тут импровизировала и
    # выдала ссылку из памяти на посторонний документ.
    searcher.act_card = AsyncMock(return_value={
        "act_nd": "102054722", "act_kind": "kodeks",
        "act_name": "Налоговый кодекс Российской Федерации. Часть первая",
        "act_number": "146-ФЗ", "act_date": "1998-07-31",
        "source_url": "http://pravo.gov.ru/proxy/ips/?docbody=&nd=102054722",
        "articles": [
            {"article_number": "1", "article_title": "Законодательство", "chapter_path": "Глава 1. ОБЩИЕ"},
            {"article_number": "2", "article_title": "Отношения", "chapter_path": "Глава 1. ОБЩИЕ"},
            {"article_number": "89", "article_title": "Выездная проверка", "chapter_path": "Глава 14. КОНТРОЛЬ"},
        ],
    })
    result = await tools["get_act_info"].tool.ainvoke({"act": "НК РФ часть 1"})
    searcher.act_card.assert_awaited_once_with("102054722")
    assert "146-ФЗ" in result and "Статей в корпусе: 3" in result
    assert "nd=102054722" in result                     # ссылка ИЗ индекса, не из памяти
    assert "Глава 14. КОНТРОЛЬ: ст. 89" in result       # структура по главам
    assert "get_statute_article" in result              # куда идти за текстом нормы


@pytest.mark.asyncio
async def test_act_card_unknown_act(tools):
    result = await tools["get_act_info"].tool.ainvoke({"act": "закон о тишине"})
    assert "в корпусе не найден" in result


@pytest.mark.asyncio
async def test_act_card_disambiguates_bare_number(tools, searcher):
    searcher.resolve_act = AsyncMock(return_value=[
        {"act_nd": "1", "act_name": "Об упразднении судов", "act_number": "77-ФЗ", "act_date": "2013-02-04"},
        {"act_nd": "2", "act_name": "О внутренних морских водах", "act_number": "77-ФЗ", "act_date": "1998-07-31"},
    ])
    result = await tools["get_act_info"].tool.ainvoke({"act": "77-ФЗ"})
    searcher.act_card.assert_not_awaited()
    assert "несколько актов" in result
