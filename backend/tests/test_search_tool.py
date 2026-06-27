from unittest.mock import MagicMock

import pytest

from app.pipelines.tools import court_practice_tool_specs
from app.rag_core.prompt import get_system_prompt
from app.search.client import OpenSearchConfig
from app.search.rrf import RankedDocument
from app.search.search import (
    CourtPracticeSearcher,
    format_decision_document,
    format_search_results,
    format_vs_crosscheck,
)


@pytest.fixture
def mock_searcher():
    client = MagicMock()
    config = OpenSearchConfig()
    return CourtPracticeSearcher(client, config)


def test_build_query_body_or_match_with_phrase_boost(mock_searcher):
    body = mock_searcher._build_query_body("снижение неустойки ст 333 ГК")
    bool_query = body["query"]["bool"]
    multi_match = bool_query["must"][0]["multi_match"]
    assert multi_match["operator"] == "or"
    # No hard minimum_should_match: a strict per-field floor risks recall.
    assert "minimum_should_match" not in multi_match
    # Score-only phrase boost lives in `should` and never filters.
    phrase = bool_query["should"][0]["match_phrase"]["act_text"]
    assert phrase["boost"] == 2.0
    assert phrase["slop"] == 2


def test_build_query_body_region_filter(mock_searcher):
    body = mock_searcher._build_query_body("неустойка", regions=[52, 77])
    filters = body["query"]["bool"]["filter"]
    assert {"terms": {"region_code": [52, 77]}} in filters


def test_build_query_body_no_region_filter_by_default(mock_searcher):
    body = mock_searcher._build_query_body("неустойка")
    # Nothing to filter on -> no filter clause, so search spans all regions.
    assert "filter" not in body["query"]["bool"]


def test_build_query_body_case_type_filter(mock_searcher):
    body = mock_searcher._build_query_body("кража", case_types=["criminal"])
    filters = body["query"]["bool"]["filter"]
    assert {"terms": {"case_type": ["criminal"]}} in filters


def test_search_tool_regions_param_carries_region_reference(mock_searcher):
    tool = court_practice_tool_specs(mock_searcher)[0].tool
    regions_doc = tool.args["regions"].get("description", "")
    # The region-number table lives on the tool parameter (cohesion), not the prompt.
    assert "52 Нижегородская область" in regions_doc
    assert "91 Крым" in regions_doc


def test_search_tool_case_types_param_carries_reference(mock_searcher):
    tool = court_practice_tool_specs(mock_searcher)[0].tool
    case_types_doc = tool.args["case_types"].get("description", "")
    # The proceeding-type vocabulary lives on the tool parameter, not the prompt.
    assert "criminal (уголовные)" in case_types_doc
    assert "civil (гражданские)" in case_types_doc


def test_system_prompt_keeps_region_logic_without_the_table():
    prompt = get_system_prompt()
    assert "Как определять регион" in prompt  # selection logic stays in the prompt
    assert "Справка по номерам регионов" not in prompt  # number table moved to the tool


def test_system_prompt_keeps_case_type_selection_logic():
    prompt = get_system_prompt()
    assert "Как определять вид судопроизводства" in prompt


def test_vs_crosscheck_uses_region_99_without_result_type(mock_searcher):
    mock_searcher._client.msearch.return_value = {
        "responses": [
            {"hits": {"hits": [
                {"_id": "vs-1", "_source": {"decision_id": "vs-1", "region_code": 99,
                                            "case_number": "5-КГ26-1", "act_text": "позиция ВС"}}
            ]}}
        ]
    }
    out = mock_searcher.vs_crosscheck_sync(["снижение неустойки"])
    assert [d.doc_id for d in out] == ["vs-1"]
    # The ВС arm always pins region 99 and never sends a result_type filter.
    body = mock_searcher._client.msearch.call_args.kwargs["body"]
    filters = body[1]["query"]["bool"]["filter"]
    assert {"terms": {"region_code": [99]}} in filters
    assert all("result_type" not in (f.get("term") or {}) for f in filters)


def test_vs_crosscheck_forwards_case_type(mock_searcher):
    # case_type is subject matter, not geography: a criminal query must
    # cross-check criminal ВС practice, so the filter IS forwarded (alongside 99).
    mock_searcher._client.msearch.return_value = {"responses": [{"hits": {"hits": []}}]}
    mock_searcher.vs_crosscheck_sync(["хищение"], case_types=["criminal"])
    body = mock_searcher._client.msearch.call_args.kwargs["body"]
    filters = body[1]["query"]["bool"]["filter"]
    assert {"terms": {"region_code": [99]}} in filters
    assert {"terms": {"case_type": ["criminal"]}} in filters


def test_format_vs_crosscheck_empty_and_dedup():
    empty = format_vs_crosscheck([])
    assert "не найдена" in empty
    # Must not point the model to external sources (would trigger web search).
    assert "Пленум" not in empty and "проверить отдельно" not in empty
    doc = RankedDocument(
        doc_id="vs-9",
        source={"decision_id": "vs-9", "case_number": "5-КГ26-9", "result_type": "overturned"},
        highlights=["фрагмент ВС"],
    )
    fresh = format_vs_crosscheck([doc])
    assert "ВЕРХОВНЫЙ СУД РФ" in fresh and "vs-9" in fresh
    deduped = format_vs_crosscheck([doc], primary_ids={"vs-9"})
    assert "vs-9" not in deduped and "основной выдаче" in deduped


@pytest.mark.asyncio
async def test_search_court_practice_appends_vs_crosscheck(mock_searcher, monkeypatch):
    async def fake_search(*args, **kwargs):
        return [RankedDocument(doc_id="reg-1", source={"decision_id": "reg-1", "case_number": "2-1/2026",
                                                       "court_name": "Райсуд"}, highlights=["рег фрагмент"])]

    async def fake_vs(queries, *, case_types=None):
        return [RankedDocument(doc_id="vs-1", source={"decision_id": "vs-1", "case_number": "5-КГ26-1",
                                                      "result_type": "overturned"}, highlights=["позиция ВС"])]

    monkeypatch.setattr(mock_searcher, "search", fake_search)
    monkeypatch.setattr(mock_searcher, "vs_crosscheck", fake_vs)
    tool = court_practice_tool_specs(mock_searcher)[0].tool
    out = await tool.ainvoke(
        {"queries": ["неустойка"], "date_from": None, "date_to": None, "result_type": None, "regions": None}
    )
    assert "reg-1" in out  # primary results preserved
    assert "ПЕРЕКРЁСТНАЯ ПРОВЕРКА: ВЕРХОВНЫЙ СУД РФ" in out  # ВС block always appended
    assert "vs-1" in out


def test_format_search_results_empty():
    assert "не найдена" in format_search_results([])


def test_format_search_results_includes_metadata():
    results = [
        RankedDocument(
            doc_id="dec-1",
            source={
                "decision_id": "dec-1",
                "case_number": "2-100/2026",
                "court_name": "Автозаводский районный суд",
                "decision_date": "2026-04-13",
                "decision_result": "Иск удовлетворен",
                "result_type": "granted",
                "category": "Трудовые споры",
                "act_text": "Полный текст решения",
            },
            highlights=["фрагмент решения"],
        )
    ]
    formatted = format_search_results(results)
    assert "dec-1" in formatted
    assert "2-100/2026" in formatted
    assert "фрагмент решения" in formatted


def test_format_decision_document_truncates_long_text():
    doc = {
        "decision_id": "dec-2",
        "case_number": "2-200/2026",
        "court_name": "Суд",
        "judge": "Судья",
        "decision_date": "2026-01-01",
        "decision_result": "Отказ",
        "result_type": "denied",
        "category": "Споры",
        "participants_names": "Истец, Ответчик",
        "act_text": "x" * 40_000,
    }
    formatted = format_decision_document(doc)
    assert "обрезан" in formatted
    assert len(formatted) < 40_000


def test_format_decision_document_includes_links_when_present():
    doc = {
        "decision_id": "dec-5",
        "case_number": "2-500/2026",
        "act_text": "Текст акта",
        "act_url": "https://example.com/acts/dec-5.pdf",
        "case_details_url": "https://example.com/cases/dec-5",
    }
    formatted = format_decision_document(doc)
    assert "Ссылка на акт: https://example.com/acts/dec-5.pdf" in formatted
    assert "Ссылка на дело: https://example.com/cases/dec-5" in formatted


def test_format_decision_document_omits_empty_links():
    doc = {
        "decision_id": "dec-6",
        "case_number": "2-600/2026",
        "act_text": "Текст акта",
        "act_url": "",
        "case_details_url": "",
    }
    formatted = format_decision_document(doc)
    assert "Ссылка" not in formatted


@pytest.mark.asyncio
async def test_search_court_practice_tool(mock_searcher, monkeypatch):
    async def fake_search(*args, **kwargs):
        return [
            RankedDocument(
                doc_id="dec-3",
                source={
                    "decision_id": "dec-3",
                    "case_number": "2-300/2026",
                    "court_name": "Суд",
                    "decision_date": "2026-02-01",
                    "decision_result": "Удовлетворен",
                    "result_type": "granted",
                    "category": "Категория",
                },
                highlights=["snippet"],
            )
        ]

    monkeypatch.setattr(mock_searcher, "search", fake_search)
    search_court_practice = court_practice_tool_specs(mock_searcher)[0].tool
    result = await search_court_practice.ainvoke(
        {"queries": ["неустойка", "просрочка поставки"], "date_from": None, "date_to": None, "result_type": None}
    )
    assert "dec-3" in result
    assert "2-300/2026" in result


@pytest.mark.asyncio
async def test_get_court_decision_tool(mock_searcher, monkeypatch):
    async def fake_get(decision_id: str):
        return {
            "decision_id": decision_id,
            "case_number": "2-400/2026",
            "court_name": "Суд",
            "judge": "Иванов",
            "decision_date": "2026-03-01",
            "decision_result": "Частично",
            "result_type": "partial",
            "category": "Категория",
            "participants_names": "A, B",
            "act_text": "Текст акта",
        }

    monkeypatch.setattr(mock_searcher, "get_decision", fake_get)
    get_court_decision = court_practice_tool_specs(mock_searcher)[1].tool
    result = await get_court_decision.ainvoke({"decision_id": "dec-4"})
    assert "dec-4" in result
    assert "Текст акта" in result
