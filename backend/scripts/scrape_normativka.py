"""Scrape statutes (нормативка) from pravo.gov.ru ИПС into a snapshot zip.

Mirrors the court-practice convention: the scraper produces a self-contained
zip artifact (kept in Object Storage for replayability), a separate indexer
loads it into OpenSearch.

Modes:

* ``--mode full``  — fetch every target act unconditionally.
* ``--mode update`` — fetch each act's wrapper (cheap, ~40-100КБ), compare its
  current rdk with the rdk already indexed in OpenSearch, and re-fetch only
  the acts whose redaction changed. New acts (no indexed rdk) are fetched.

Safety: for acts from the built-in codex table the fetched document title is
verified against the canonical name — a mismatched nd fails the act loudly
instead of silently ingesting the wrong document. Every failure is reported
and the exit code is non-zero if anything failed (no silent partial success).

Usage:
    uv run python scripts/scrape_normativka.py --out /tmp/normativka.zip
    uv run python scripts/scrape_normativka.py --out /tmp/upd.zip \
        --mode update --opensearch-url http://1.2.3.4:9200
"""
import argparse
import json
import logging
import sys
import zipfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.normativka.acts import CODICES, KnownAct, normalize_ref  # noqa: E402
from app.normativka.fetch import ActMeta, fetch_act_meta, fetch_act_text  # noqa: E402
from app.normativka.ips_client import IpsClient, IpsError  # noqa: E402
from app.normativka.parse import split_articles  # noqa: E402
from app.search.normativka_index import NORMATIVKA_INDEX_ALIAS  # noqa: E402

logger = logging.getLogger("scrape_normativka")

# Title sanity check: the first N normalized chars of the canonical name must
# appear in the fetched wrapper title. Codex titles in the ИПС match their
# canonical names, so this catches a re-pointed/stale nd without being brittle
# about punctuation. Normalization is shared with the runtime alias resolver
# (acts.normalize_ref) so the two rules cannot drift apart.
_TITLE_CHECK_CHARS = 20


def _title_matches(expected_name: str, fetched_title: str) -> bool:
    return normalize_ref(expected_name)[:_TITLE_CHECK_CHARS] in normalize_ref(fetched_title)


def _indexed_rdk_by_nd(opensearch_url: str, alias: str) -> dict[str, str]:
    """Current rdk per act in the live index (for --mode update)."""
    from app.search.client import OpenSearchConfig, build_opensearch_client

    client = build_opensearch_client(OpenSearchConfig(url=opensearch_url))
    if not client.indices.exists_alias(name=alias):
        logger.info("Индекс %s ещё не существует — все акты считаются новыми", alias)
        return {}
    body = {
        "size": 0,
        "aggs": {
            "acts": {
                "terms": {"field": "act_nd", "size": 10_000},
                "aggs": {"rdk": {"terms": {"field": "rdk", "size": 10}}},
            }
        },
    }
    response = client.search(index=alias, body=body)
    result: dict[str, str] = {}
    for bucket in response.get("aggregations", {}).get("acts", {}).get("buckets", []):
        rdk_buckets = bucket.get("rdk", {}).get("buckets", [])
        if len(rdk_buckets) == 1:
            result[bucket["key"]] = rdk_buckets[0]["key"]
        elif rdk_buckets:
            # Mixed rdk values = a previous index run died between bulk load
            # and the superseded-articles purge. Whatever the majority rdk is,
            # the act's state is inconsistent — leave it out of the map so it
            # counts as changed and gets re-scraped/re-purged this run.
            logger.warning(
                "Акт %s в индексе со смешанными rdk (%s) — будет пересобран",
                bucket["key"], [b["key"] for b in rdk_buckets],
            )
    return result


def scrape_act(client: IpsClient, act: KnownAct, *, meta: ActMeta | None = None) -> dict:
    """Fetch and parse one act; returns the per-act snapshot payload.

    ``meta`` lets update mode reuse the wrapper it already fetched for the rdk
    comparison instead of paying a second round-trip to a flaky portal.
    """
    if meta is None:
        meta = fetch_act_meta(client, act.nd)
    if not _title_matches(act.name, meta.title):
        raise IpsError(
            f"nd={act.nd}: заголовок «{meta.title}» не похож на «{act.name}» — "
            "nd указывает не на тот документ, ингест остановлен"
        )
    html = fetch_act_text(client, act.nd, rdk=meta.current_rdk)
    articles = split_articles(html)
    if not articles:
        raise IpsError(f"nd={act.nd}: из текста не вырезано ни одной статьи")
    return {
        "act": {
            "nd": act.nd,
            "kind": act.kind,
            "name": act.name,
            "aliases": list(act.aliases),
            "number": act.number,
            "date": act.adoption_date,
            "rdk": meta.current_rdk,
            "title_fetched": meta.title,
        },
        "articles": [asdict(article) for article in articles],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape нормативка from pravo.gov.ru ИПС")
    parser.add_argument("--out", required=True, help="Path of the snapshot zip to write")
    parser.add_argument(
        "--acts",
        default="kodeksy",
        help="'kodeksy' (все кодексы из встроенной таблицы) или список nd через запятую",
    )
    parser.add_argument("--mode", choices=["full", "update"], default="full")
    parser.add_argument("--opensearch-url", help="Нужен для --mode update (сравнение rdk)")
    parser.add_argument("--index-alias", default=NORMATIVKA_INDEX_ALIAS)
    parser.add_argument("--pause", type=float, default=1.5, help="Пауза между запросами к ИПС, сек")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.acts == "kodeksy":
        targets = list(CODICES)
    else:
        by_nd = {act.nd: act for act in CODICES}
        targets = []
        for nd in [part.strip() for part in args.acts.split(",") if part.strip()]:
            if nd not in by_nd:
                logger.error("nd=%s не найден во встроенной таблице актов", nd)
                return 2
            targets.append(by_nd[nd])

    indexed_rdk: dict[str, str] = {}
    if args.mode == "update":
        if not args.opensearch_url:
            parser.error("--mode update требует --opensearch-url")
        indexed_rdk = _indexed_rdk_by_nd(args.opensearch_url, args.index_alias)

    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    written: list[dict] = []
    skipped: list[str] = []
    failed: list[tuple[str, str]] = []

    with IpsClient(pause=args.pause) as client, zipfile.ZipFile(
        args.out, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for act in targets:
            try:
                meta = None
                if args.mode == "update":
                    meta = fetch_act_meta(client, act.nd)
                    if indexed_rdk.get(act.nd) == meta.current_rdk:
                        logger.info("%s: rdk=%s не изменился — пропуск", act.name, meta.current_rdk)
                        skipped.append(act.nd)
                        continue
                payload = scrape_act(client, act, meta=meta)
                payload["act"]["fetched_at"] = fetched_at
                archive.writestr(f"acts/{act.nd}.json", json.dumps(payload, ensure_ascii=False))
                written.append(
                    {
                        "nd": act.nd,
                        "name": act.name,
                        "rdk": payload["act"]["rdk"],
                        "articles": len(payload["articles"]),
                    }
                )
                logger.info(
                    "%s: rdk=%s, статей=%d", act.name, payload["act"]["rdk"], len(payload["articles"])
                )
            except IpsError as exc:
                logger.error("%s (nd=%s): %s", act.name, act.nd, exc)
                failed.append((act.nd, str(exc)))

        archive.writestr(
            "manifest.json",
            json.dumps(
                {"fetched_at": fetched_at, "mode": args.mode, "acts": written, "failed": failed},
                ensure_ascii=False,
                indent=2,
            ),
        )

    logger.info(
        "Готово: %d актов записано, %d пропущено (без изменений), %d с ошибками",
        len(written), len(skipped), len(failed),
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
