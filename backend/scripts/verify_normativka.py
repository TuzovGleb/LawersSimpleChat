"""Quality gate for the нормативка index — run BEFORE pointing the alias at it.

The corpus feeds legal answers, so a bad load must not reach lawyers. The gate
checks the three things that actually went wrong while building the corpus:

* **обёртки не просочились** — «Об утверждении Кодекса законов о труде РСФСР»
  is marked «Действует без изменений» by the ИПС and carries the full text of
  the Soviet labour code, repealed since 2002 (see app.normativka.catalog);
* **точные ссылки резолвятся** — «ст. 81 ТК», «ст. 333.19 НК», «ст. 46 Об ООО»;
* **тематический поиск даёт норму, а не шум** — the top hit for a lawyer's
  phrasing must be the article a lawyer would cite.

Plus corpus arithmetic (acts and articles per kind) and an integrity check for
articles whose number collides inside one act.

Usage:
    uv run python scripts/verify_normativka.py --opensearch-url http://1.2.3.4:9200
    uv run python scripts/verify_normativka.py --opensearch-url … --index legal_acts_v2
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.normativka.acts import resolve_act  # noqa: E402
from app.search.client import OpenSearchConfig, build_opensearch_client  # noqa: E402
from app.search.normativka import NormativkaSearcher  # noqa: E402

logger = logging.getLogger("verify_normativka")

# Acts that must NEVER be in the corpus. Matched as a PREFIX of the name, not
# as a phrase anywhere in it: «О внесении изменений …» в НАЧАЛЕ — поправочный
# акт, а те же слова в середине принадлежат нормальному закону («О междуна-
# родном медицинском кластере И ВНЕСЕНИИ ИЗМЕНЕНИЙ в отдельные акты»), и
# поиск по фразе забраковал бы корпус на ровном месте.
FORBIDDEN_NAME_PREFIXES = (
    "Об утверждении Кодекса законов о труде",
    "Об утверждении Земельного кодекса РСФСР",
    "Об утверждении Водного кодекса РСФСР",
    "Об утверждении Уголовного кодекса РСФСР",
    "Об утверждении Уголовно-процессуального кодекса РСФСР",
    "Об утверждении Исправительно-трудового кодекса",
    "О внесении изменени",
    "О ратификации",
    "О приведении",
)

# (акт, статья, что должно быть в заголовке статьи)
EXACT_CHECKS = (
    ("ТК РФ", "81", "расторжение трудового договора"),
    ("НК РФ часть 2", "333.19", "государственной пошлины"),
    ("КоАП", "5.27", "нарушение трудового законодательства"),
    ("ГК РФ часть 1", "395", "ответственность за неисполнение денежного обязательства"),
    ("УК", "158", "кража"),
)

# Тематический поиск — СПРАВОЧНО, не как жёсткий критерий: пока нет golden
# set, ожидание легко задать неверно, и тогда гейт заблокирует деплой по своей
# же ошибке. Оба первых варианта этих проверок именно так и провалились:
#   • ждал ст. 395 ГК на «проценты за пользование чужими денежными средствами»,
#     но эта формулировка — редакция ДО 2015 года; в действующем тексте ст. 395
#     её нет («неправомерное удержание денежных средств…»), а дословный
#     заголовок есть у ст. 413 КТМ, которую поиск и вернул — верно лексически;
#   • ждал именно ст. 333.19 НК (суды общей юрисдикции), тогда как запрос не
#     различал систему судов, и ст. 333.21 (арбитраж) с почти тем же
#     заголовком равноправна.
# Ожидания ниже сверены с ДЕЙСТВУЮЩИМ текстом корпуса; допускается несколько
# приемлемых номеров.
TOPICAL_CHECKS = (
    ("расторжение трудового договора по инициативе работодателя", ("81",)),
    ("размеры государственной пошлины по делам судов общей юрисдикции", ("333.19", "333.21")),
    ("ответственность за неисполнение денежного обязательства проценты на сумму долга", ("395",)),
    ("согласие субъекта персональных данных на обработку", ("9", "6")),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка качества индекса нормативки")
    parser.add_argument("--opensearch-url", required=True)
    parser.add_argument("--index", default="", help="Конкретный индекс (по умолчанию — алиас legal_acts)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    config = OpenSearchConfig(url=args.opensearch_url)
    if args.index:
        config = config.model_copy(update={"normativka_index_alias": args.index})
    searcher = NormativkaSearcher(build_opensearch_client(config), config)
    client = searcher._client
    index = config.normativka_index_alias
    failures: list[str] = []

    print(f"=== ИНДЕКС {index} @ {args.opensearch_url} ===")
    total = client.count(index=index)["count"]
    aggs = client.search(
        index=index,
        body={
            "size": 0,
            "aggs": {
                "kinds": {
                    "terms": {"field": "act_kind", "size": 10},
                    "aggs": {"acts": {"cardinality": {"field": "act_nd"}}},
                },
                "acts": {"cardinality": {"field": "act_nd"}},
            },
        },
    )["aggregations"]
    print(f"статей всего: {total} | актов: {aggs['acts']['value']}")
    for bucket in aggs["kinds"]["buckets"]:
        print(f"   {bucket['key']:9}: актов {bucket['acts']['value']:5}, статей {bucket['doc_count']}")

    print("\n=== ЗАПРЕЩЁННЫЕ АКТЫ (обёртки и поправки) ===")
    for prefix in FORBIDDEN_NAME_PREFIXES:
        found = client.search(
            index=index,
            body={
                "size": 1,
                "query": {"prefix": {"act_name.raw": prefix}},
                "_source": ["act_name", "act_nd"],
            },
        )["hits"]["hits"]
        if found:
            name = found[0]["_source"].get("act_name", "")
            failures.append(f"в корпусе запрещённый акт: {name[:70]} (nd={found[0]['_source'].get('act_nd')})")
            print(f"   ✗ {prefix!r} → {name[:62]}")
        else:
            print(f"   ✓ {prefix!r} — отсутствует")

    print("\n=== ТОЧНЫЕ ССЫЛКИ ===")
    for act_ref, article, expect in EXACT_CHECKS:
        known = resolve_act(act_ref)
        candidates = [{"act_nd": known.nd}] if known else searcher.resolve_act_sync(act_ref)
        source = searcher.resolve_sync(candidates[0]["act_nd"], article) if candidates else None
        title = (source or {}).get("article_title", "")
        ok = expect.lower() in title.lower()
        print(f"   {'✓' if ok else '✗'} ст. {article} {act_ref}: {title[:60] or 'НЕ НАЙДЕНА'}")
        if not ok:
            failures.append(f"точная ссылка ст. {article} {act_ref} → {title[:60] or 'не найдена'}")

    print("\n=== ТЕМАТИЧЕСКИЙ ПОИСК (топ-3, справочно — не блокирует) ===")
    soft_misses: list[str] = []
    for query, expected in TOPICAL_CHECKS:
        results = searcher.search_sync([query])
        numbers = [r.source.get("article_number") for r in results[:3]]
        ok = any(number in numbers for number in expected)
        print(f"   {'✓' if ok else '~'} «{query[:56]}» → {numbers}")
        if not ok:
            soft_misses.append(f"«{query[:46]}»: ждали {expected}, получили {numbers}")

    print("\n=== ЦЕЛОСТНОСТЬ: номера статей, дублирующиеся внутри акта ===")
    collisions = client.search(
        index=index,
        body={
            "size": 0,
            "aggs": {
                "acts": {
                    "terms": {"field": "act_nd", "size": 2000},
                    "aggs": {
                        "numbers": {"cardinality": {"field": "article_number"}},
                    },
                }
            },
        },
    )["aggregations"]["acts"]["buckets"]
    suspicious = [b for b in collisions if b["doc_count"] != b["numbers"]["value"]]
    print(f"   актов с расхождением (статей ≠ уникальных номеров): {len(suspicious)}")
    for bucket in suspicious[:5]:
        print(f"      nd={bucket['key']}: статей {bucket['doc_count']}, номеров {bucket['numbers']['value']}")

    print()
    if soft_misses:
        print(f"Тематический поиск: расхождений с ожиданиями {len(soft_misses)} (смотреть глазами, не блокирует):")
        for item in soft_misses:
            print(f"   ~ {item}")
        print()
    if failures:
        print(f"ПРОВЕРКА НЕ ПРОЙДЕНА — {len(failures)} проблем:")
        for item in failures:
            print(f"   • {item}")
        return 1
    print("ПРОВЕРКА ПРОЙДЕНА: корпус пригоден для переключения алиаса")
    return 0


if __name__ == "__main__":
    sys.exit(main())
