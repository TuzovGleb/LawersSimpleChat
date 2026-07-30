"""Индексатор: коллизии номеров статей внутри одного акта."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "index_normativka", Path(__file__).resolve().parent.parent / "scripts" / "index_normativka.py"
)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
dedupe_articles = _module.dedupe_articles


def _doc(number: str, title: str, text: str) -> dict:
    return {"article_number": number, "article_title": title, "article_text": text}


def test_substantive_article_wins_over_repeal_stub():
    # Реальный случай БК: две «Статьи 242.1» — историческая заглушка и
    # действующая норма. id = sha(nd|номер), поэтому одна перезаписывает
    # другую; победитель не должен зависеть от порядка загрузки.
    stub = _doc("242.1", "", "Статья 242.1. (Дополнение статьей - ФЗ от 03.01.2006 № 6-ФЗ) (Утратила силу)")
    live = _doc("242.1", "Общие положения", "Статья 242.1. Общие положения. 1. Исполнение судебных актов " * 10)
    for order in ([stub, live], [live, stub]):
        result = dedupe_articles(list(order), "Бюджетный кодекс")
        assert len(result) == 1
        assert result[0]["article_title"] == "Общие положения"


def test_unique_numbers_are_untouched_and_ordered():
    docs = [_doc("1", "Первая", "текст 1"), _doc("2", "Вторая", "текст 2"), _doc("2.1", "", "текст 2.1")]
    result = dedupe_articles(list(docs), "Некий акт")
    assert [d["article_number"] for d in result] == ["1", "2", "2.1"]


def test_longer_text_wins_when_neither_has_title():
    short, long = _doc("5", "", "коротко"), _doc("5", "", "гораздо длиннее " * 20)
    assert dedupe_articles([short, long], "акт")[0]["article_text"].startswith("гораздо")
    assert dedupe_articles([long, short], "акт")[0]["article_text"].startswith("гораздо")


def test_purge_keeps_current_articles_and_removes_the_rest():
    from unittest.mock import MagicMock
    client = MagicMock()
    client.delete_by_query.return_value = {"deleted": 3}
    deleted = _module.purge_stale_articles(client, "legal_acts_v2", "102074279", ["id1", "id2"])
    body = client.delete_by_query.call_args.kwargs["body"]
    assert deleted == 3
    assert {"term": {"act_nd": "102074279"}} in body["query"]["bool"]["filter"]
    # Чистим по ИДЕНТИФИКАТОРАМ текущей загрузки, а не по rdk: при перепарсинге
    # той же редакции документ с неверным номером иначе остался бы навсегда.
    assert body["query"]["bool"]["must_not"] == [{"terms": {"article_id": ["id1", "id2"]}}]


def test_purge_with_no_articles_clears_the_act():
    from unittest.mock import MagicMock
    client = MagicMock()
    client.delete_by_query.return_value = {"deleted": 7}
    _module.purge_stale_articles(client, "legal_acts_v2", "1", [])
    assert _module and client.delete_by_query.call_args.kwargs["body"]["query"]["bool"]["must_not"] == []
