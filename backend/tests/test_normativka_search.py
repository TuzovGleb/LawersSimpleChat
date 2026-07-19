from unittest.mock import MagicMock

import pytest

from app.search.client import OpenSearchConfig
from app.search.normativka import (
    FZ_SPECIFICITY_BOOST,
    NormativkaSearcher,
    format_statute_article,
    format_statute_results,
)
from app.search.normativka_index import (
    generate_article_id,
    normalize_article,
    normalize_article_number,
)
from app.search.rrf import RankedDocument


@pytest.fixture
def searcher():
    return NormativkaSearcher(MagicMock(), OpenSearchConfig())


def test_query_body_field_boosts(searcher):
    body = searcher._build_query_body("расторжение трудового договора")
    multi_match = body["query"]["bool"]["must"][0]["multi_match"]
    # Structure exploitation: article title is the densest signal.
    assert multi_match["fields"][0] == "article_title^4"
    assert "act_name^2.5" in multi_match["fields"]
    assert multi_match["operator"] == "or"
    assert "minimum_should_match" not in multi_match


def test_query_body_phrase_and_fz_boosts_are_score_only(searcher):
    body = searcher._build_query_body("неустойка")
    should = body["query"]["bool"]["should"]
    phrase = should[0]["match_phrase"]["article_text"]
    assert phrase["boost"] == 2.0 and phrase["slop"] == 2
    # Lex-specialis tie-break: small, score-only, never filters.
    fz = should[1]["term"]["act_kind"]
    assert fz["value"] == "fz"
    assert fz["boost"] == FZ_SPECIFICITY_BOOST < 1.0
    assert "filter" not in body["query"]["bool"]


def test_query_body_act_filter(searcher):
    body = searcher._build_query_body("увольнение", act_nds=["102074279"])
    assert {"terms": {"act_nd": ["102074279"]}} in body["query"]["bool"]["filter"]


def test_resolve_uses_term_filters_without_scoring(searcher):
    searcher._client.search.return_value = {"hits": {"hits": []}}
    searcher.resolve_sync("102074279", "ст. 81")
    body = searcher._client.search.call_args.kwargs["body"]
    filters = body["query"]["bool"]["filter"]
    assert {"term": {"act_nd": "102074279"}} in filters
    # «ст. 81» decorations are stripped before the term match.
    assert {"term": {"article_number": "81"}} in filters
    assert "should" not in body["query"]["bool"]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("81", "81"),
        ("ст. 81", "81"),
        ("Статья 333.19", "333.19"),
        ("333.34-1", "333.34.1"),
        ("15.34 - 1", "15.34.1"),
    ],
)
def test_normalize_article_number(raw, expected):
    assert normalize_article_number(raw) == expected


def test_normalize_article_builds_document():
    act = {
        "nd": "102074279",
        "kind": "kodeks",
        "name": "Трудовой кодекс Российской Федерации",
        "aliases": ["ТК РФ", "ТК"],
        "number": "197-ФЗ",
        "date": "2001-12-30",
        "rdk": "118",
    }
    doc = normalize_article(
        {"number": "81", "title": "Расторжение…", "text": "Текст статьи", "chapter_path": "Глава 13"},
        act=act,
        indexed_at="2026-07-19",
    )
    assert doc["_id"] == generate_article_id("102074279", "81")
    assert doc["act_kind"] == "kodeks"
    assert doc["act_aliases"] == "ТК РФ, ТК"
    assert doc["rdk"] == "118"
    assert doc["source_url"].endswith("nd=102074279")


def test_normalize_article_rejects_empty():
    assert normalize_article({"number": "", "text": "x"}, act={"nd": "1"}, indexed_at="") is None
    assert normalize_article({"number": "1", "text": ""}, act={"nd": "1"}, indexed_at="") is None


def _ranked(source: dict) -> RankedDocument:
    return RankedDocument(doc_id=source.get("article_id", "id"), source=source, highlights=["фрагмент"])


def test_format_results_show_reference_and_snippet():
    text = format_statute_results(
        [
            _ranked(
                {
                    "article_id": "abc",
                    "act_name": "Трудовой кодекс Российской Федерации",
                    "act_number": "197-ФЗ",
                    "article_number": "81",
                    "article_title": "Расторжение трудового договора",
                    "chapter_path": "Глава 13",
                }
            )
        ]
    )
    assert "ст. 81 — Трудовой кодекс Российской Федерации (197-ФЗ)" in text
    assert "фрагмент" in text


def test_format_article_includes_source_and_redaction_note():
    text = format_statute_article(
        {
            "article_id": "abc",
            "act_name": "Трудовой кодекс Российской Федерации",
            "article_number": "81",
            "article_title": "Расторжение…",
            "article_text": "Полный текст",
            "source_url": "http://pravo.gov.ru/proxy/ips/?docbody=&nd=102074279",
        }
    )
    assert "Официальный текст: http://pravo.gov.ru" in text
    assert "действующей редакции" in text
    assert text.endswith("Полный текст")
