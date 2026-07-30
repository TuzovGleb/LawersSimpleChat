"""Load a нормативка snapshot zip (from scrape_normativka.py) into OpenSearch.

Per-act idempotency: article ids are stable (sha of nd|number), so re-indexing
an act overwrites its articles in place; afterwards every article of that act
NOT written by this load is purged — it belongs either to a superseded
redaction or to an earlier, wrong parse of the same one.

Usage:
    uv run python scripts/index_normativka.py \
        --source /tmp/normativka.zip --opensearch-url http://1.2.3.4:9200
"""
import argparse
import json
import logging
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.search.client import OpenSearchConfig, build_opensearch_client  # noqa: E402
from app.search.index import bulk_index_documents, ensure_index, set_index_refresh  # noqa: E402
from app.search.normativka_index import (  # noqa: E402
    NORMATIVKA_INDEX_ALIAS,
    NORMATIVKA_INDEX_BODY,
    NORMATIVKA_INDEX_VERSION,
    normalize_article,
)

logger = logging.getLogger("index_normativka")


def dedupe_articles(documents: list[dict], act_label: str) -> list[dict]:
    """Resolve articles that share a number inside one act, deterministically.

    Document ids are sha(nd|номер), so same-numbered articles overwrite each
    other and the survivor would otherwise depend on load order. It happens in
    the documents themselves, not only in parsing: Бюджетный кодекс carries two
    «Статьи 242.1» — a historical stub «(Дополнение статьей … ) (Утратила
    силу…)» and the live «Общие положения». The substantive one must win every
    time (titled first, then longer text), never «whichever came last», or a
    lawyer gets a repeal stub in place of the norm.
    """
    by_number: dict[str, dict] = {}
    dropped: list[str] = []
    for doc in documents:
        number = doc["article_number"]
        previous = by_number.get(number)
        if previous is None:
            by_number[number] = doc
            continue
        rank = lambda d: (bool(d.get("article_title")), len(d.get("article_text") or ""))  # noqa: E731
        winner = previous if rank(previous) >= rank(doc) else doc
        by_number[number] = winner
        dropped.append(number)
    if dropped:
        logger.warning(
            "%s: номера статей повторяются %s — оставлена содержательная версия каждой",
            act_label, sorted(set(dropped))[:10],
        )
    return list(by_number.values())


def purge_stale_articles(client, index_name: str, act_nd: str, keep_ids: list[str]) -> int:
    """Delete every article of the act except the ones just written.

    Keyed on the ids of the current load rather than on rdk, because articles
    go stale for two different reasons and only one of them changes the rdk:

    * новая редакция акта — статья могла исчезнуть из текста;
    * ПЕРЕПАРСИНГ той же редакции — если прежний разбор дал неверный номер
      (отвалившийся надстрочный индекс превращал «13¹» в «13»), документ с
      этим номером остаётся в индексе навсегда и подменяет настоящую статью.

    Возвращает число удалённых документов.
    """
    must_not = [{"terms": {"article_id": keep_ids}}] if keep_ids else []
    body = {"query": {"bool": {"filter": [{"term": {"act_nd": act_nd}}], "must_not": must_not}}}
    response = client.delete_by_query(index=index_name, body=body, conflicts="proceed")
    return response.get("deleted", 0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Index нормативка snapshot into OpenSearch")
    parser.add_argument("--source", help="Snapshot zip from scrape_normativka.py (не нужен для --swap-alias-only)")
    parser.add_argument("--opensearch-url", required=True)
    parser.add_argument("--index-name", default=NORMATIVKA_INDEX_VERSION)
    parser.add_argument("--alias", default=NORMATIVKA_INDEX_ALIAS)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument(
        "--no-alias",
        action="store_true",
        help="Создать/наполнить индекс, НЕ переключая на него алиас — рабочий индекс "
        "остаётся прежним, пока новый не пройдёт проверку качества",
    )
    parser.add_argument(
        "--swap-alias-only",
        action="store_true",
        help="Ничего не загружать, только перевести алиас на --index-name (после проверки)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if not args.swap_alias_only and not args.source:
        parser.error("--source обязателен, если это не --swap-alias-only")

    client = build_opensearch_client(OpenSearchConfig(url=args.opensearch_url))

    if args.swap_alias_only:
        if not client.indices.exists(index=args.index_name):
            logger.error("Индекс %s не существует — переключать алиас не на что", args.index_name)
            return 2
        count = client.count(index=args.index_name).get("count", 0)
        if not count:
            logger.error("Индекс %s пуст — отказываюсь переводить на него алиас", args.index_name)
            return 2
        ensure_index(client, index_name=args.index_name, alias=args.alias, body=NORMATIVKA_INDEX_BODY)
        logger.info("Алиас %s → %s (документов: %d)", args.alias, args.index_name, count)
        return 0

    # Загрузка без переключения алиаса: ensure_index иначе переводит алиас
    # СРАЗУ, и прод оказался бы на полупустом индексе в момент заливки.
    if args.no_alias:
        if not client.indices.exists(index=args.index_name):
            client.indices.create(index=args.index_name, body=NORMATIVKA_INDEX_BODY)
            logger.info("Создан индекс %s (алиас не трогаем)", args.index_name)
    else:
        ensure_index(client, index_name=args.index_name, alias=args.alias, body=NORMATIVKA_INDEX_BODY)

    total_indexed = 0
    total_purged = 0
    total_errors: list = []

    set_index_refresh(client, index_name=args.index_name, interval="-1")
    try:
        with zipfile.ZipFile(args.source) as archive:
            act_files = sorted(n for n in archive.namelist() if n.startswith("acts/") and n.endswith(".json"))
            if not act_files:
                # A legitimate outcome of an update run: nothing changed since
                # the last scrape, the snapshot holds only the manifest.
                if "manifest.json" in archive.namelist():
                    logger.info("Снапшот без изменений (только manifest) — индексировать нечего")
                    return 0
                logger.error("В снапшоте нет ни acts/*.json, ни manifest.json — это не наш формат")
                return 2
            for name in act_files:
                payload = json.loads(archive.read(name))
                act = payload["act"]
                indexed_at = act.get("fetched_at") or ""
                documents = []
                for raw_article in payload.get("articles", []):
                    doc = normalize_article(raw_article, act=act, indexed_at=indexed_at)
                    if doc:
                        documents.append(doc)
                documents = dedupe_articles(documents, act.get("name") or act["nd"])
                for start in range(0, len(documents), args.batch_size):
                    batch = documents[start : start + args.batch_size]
                    success, errors = bulk_index_documents(client, batch, index_name=args.index_name)
                    total_indexed += success
                    total_errors.extend(errors)
                purged = purge_stale_articles(
                    client, args.index_name, act["nd"], [d["_id"] for d in documents]
                )
                total_purged += purged
                logger.info(
                    "%s: статей %d, вычищено устаревших %d",
                    act.get("name") or act["nd"], len(documents), purged,
                )
    finally:
        set_index_refresh(client, index_name=args.index_name, interval="1s")
        client.indices.refresh(index=args.index_name)

    # Считаем по конкретному индексу: при --no-alias алиас ещё смотрит на старый.
    count = client.count(index=args.index_name).get("count")
    logger.info(
        "Готово: проиндексировано %d, вычищено %d, ошибок %d; всего в индексе: %s",
        total_indexed, total_purged, len(total_errors), count,
    )
    if total_errors:
        logger.error("Первые ошибки: %s", total_errors[:3])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
