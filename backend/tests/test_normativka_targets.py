"""Выбор целей скрейпа: из таблицы, из каталога, из индекса; шарды и --only-new."""
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

_spec = importlib.util.spec_from_file_location(
    "scrape_normativka", Path(__file__).resolve().parent.parent / "scripts" / "scrape_normativka.py"
)
scrape = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scrape)


def test_indexed_spec_takes_targets_from_index_without_touching_portal():
    # Ночному обновлению 1400 актов незачем заново обходить каталог портала
    # (~570 запросов, час) — состав корпуса уже известен индексу.
    client = MagicMock()
    indexed = [scrape.ScrapeTarget("1", "fz", "О чём-то", "44-ФЗ", "2013-04-05")]
    targets = scrape.build_targets(client, "indexed", indexed=indexed)
    assert targets == indexed
    client.get_text.assert_not_called()


def test_kodeksy_spec_needs_no_index_and_no_catalog():
    client = MagicMock()
    targets = scrape.build_targets(client, "kodeksy")
    assert len(targets) == 25
    assert all(t.kind == "kodeks" for t in targets)
    client.get_text.assert_not_called()


def test_shards_partition_the_corpus_without_loss_or_overlap():
    nds = [str(n) for n in range(1000)]
    shards = [{nd for nd in nds if scrape._shard_of(nd, 7) == i} for i in range(7)]
    union = set().union(*shards)
    assert union == set(nds)                      # ни один акт не потерян
    assert sum(len(s) for s in shards) == len(nds)  # и ни один не задвоен
    assert all(shards)                              # шарды непустые


def test_shard_assignment_is_stable_across_runs():
    assert scrape._shard_of("102074279", 7) == scrape._shard_of("102074279", 7)


def test_indexed_acts_reads_metadata_and_rdk():
    client = MagicMock()
    client.indices.exists_alias.return_value = True
    client.search.return_value = {
        "aggregations": {"acts": {"buckets": [
            {"key": "102074279", "doc_count": 538,
             "rdk": {"buckets": [{"key": "192"}]},
             "meta": {"hits": {"hits": [{"_source": {
                 "act_nd": "102074279", "act_kind": "kodeks",
                 "act_name": "Трудовой кодекс Российской Федерации",
                 "act_number": "197-ФЗ", "act_date": "2001-12-30"}}]}}},
            # смешанные rdk = прошлая индексация оборвалась; акт должен
            # попасть в цели, но НЕ в карту rdk (иначе будет считаться свежим)
            {"key": "102051516", "doc_count": 65,
             "rdk": {"buckets": [{"key": "56"}, {"key": "55"}]},
             "meta": {"hits": {"hits": [{"_source": {"act_nd": "102051516", "act_name": "Об ООО"}}]}}},
        ]}}
    }
    import app.search.client as client_module
    orig = client_module.build_opensearch_client
    client_module.build_opensearch_client = lambda config: client
    try:
        targets, rdk = scrape.indexed_acts("http://x:9200", "legal_acts")
    finally:
        client_module.build_opensearch_client = orig
    assert [t.nd for t in targets] == ["102074279", "102051516"]
    assert targets[0].number == "197-ФЗ" and targets[0].kind == "kodeks"
    assert rdk == {"102074279": "192"}
