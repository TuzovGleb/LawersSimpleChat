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


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("44-ФЗ", "44-ФЗ"),
        ("44 ФЗ", "44-ФЗ"),
        ("44фз", "44-ФЗ"),
        ("№ 152-ФЗ", "152-ФЗ"),
        ("1-ФКЗ", "1-ФКЗ"),
        ("2300-I", "2300-I"),
        ("127", "127"),
        ("О защите прав потребителей", ""),
    ],
)
def test_normalize_act_number(raw, expected):
    from app.search.normativka_index import normalize_act_number
    assert normalize_act_number(raw) == expected


def test_act_number_is_searchable(searcher):
    body = searcher._build_query_body("44-ФЗ закупки")
    assert "act_number.text^2.5" in body["query"]["bool"]["must"][0]["multi_match"]["fields"]


def test_resolve_act_matches_number_and_name(searcher):
    searcher._client.search.return_value = {"aggregations": {"acts": {"buckets": []}}}
    searcher.resolve_act_sync("44-ФЗ")
    body = searcher._client.search.call_args.kwargs["body"]
    should = body["query"]["bool"]["should"]
    # точный номер — сильнейший сигнал, плюс совпадения по названию/алиасам
    assert {"term": {"act_number": {"value": "44-ФЗ", "boost": 10.0}}} in should
    assert any("act_name" in list(clause.values())[0] for clause in should)
    assert body["query"]["bool"]["minimum_should_match"] == 1


def test_resolve_act_by_name_has_no_number_clause(searcher):
    searcher._client.search.return_value = {"aggregations": {"acts": {"buckets": []}}}
    searcher.resolve_act_sync("О защите прав потребителей")
    should = searcher._client.search.call_args.kwargs["body"]["query"]["bool"]["should"]
    assert not any("act_number" in list(c.values())[0] for c in should)


def test_resolve_act_empty_ref_makes_no_query(searcher):
    assert searcher.resolve_act_sync("  ") == []
    searcher._client.search.assert_not_called()


def test_curated_aliases_are_merged_into_the_document():
    # «об ООО» лексически не пересекается с «Об обществах с ограниченной
    # ответственностью» — без алиасов такой запрос не найдёт акт вовсе.
    act = {"nd": "102051516", "kind": "fz", "name": "Об обществах с ограниченной ответственностью",
           "number": "14-ФЗ", "date": "1998-02-08", "rdk": "56"}
    doc = normalize_article({"number": "46", "title": "Крупные сделки", "text": "…"}, act=act, indexed_at="")
    assert "ООО" in doc["act_aliases"]


def test_aliases_do_not_leak_to_a_namesake_number():
    # 14-ФЗ носят несколько актов; алиасы ООО не должны попасть к однофамильцу.
    act = {"nd": "102136033", "kind": "fz", "name": "Об упразднении некоторых районных судов",
           "number": "14-ФЗ", "date": "2013-02-04", "rdk": "0"}
    doc = normalize_article({"number": "1", "title": "", "text": "…"}, act=act, indexed_at="")
    assert doc["act_aliases"] == ""
