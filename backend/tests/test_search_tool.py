from unittest.mock import MagicMock

import pytest

from app.pipelines.tools import court_practice_tool_specs
from app.search.client import OpenSearchConfig
from app.search.rrf import RankedDocument
from app.search.search import CourtPracticeSearcher, format_decision_document, format_search_results


@pytest.fixture
def mock_searcher():
    client = MagicMock()
    config = OpenSearchConfig()
    return CourtPracticeSearcher(client, config)


def test_build_query_body_requires_terms_and_boosts_phrases(mock_searcher):
    body = mock_searcher._build_query_body("снижение неустойки ст 333 ГК")
    bool_query = body["query"]["bool"]
    assert bool_query["must"][0]["multi_match"]["minimum_should_match"] == "2<75%"
    phrase = bool_query["should"][0]["match_phrase"]["act_text"]
    assert phrase["boost"] == 2.0
    assert phrase["slop"] == 2


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
