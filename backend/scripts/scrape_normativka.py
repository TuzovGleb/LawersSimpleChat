"""Scrape statutes (нормативка) from pravo.gov.ru ИПС into a snapshot zip.

Mirrors the court-practice convention: the scraper produces a self-contained
zip artifact (kept in Object Storage for replayability), a separate indexer
loads it into OpenSearch.

Targets (``--acts``):

* ``kodeksy``  — the 25 codices from the curated table (they carry the aliases
  lawyers use: «ТК РФ», «ГПК»);
* ``fz`` / ``fkz`` / ``zakony`` — enumerated LIVE from the ИПС catalog by вид
  документа, minus technical acts (поправки/ратификации/отмены — 91% of the
  ФЗ corpus, their text is «в статье 5 слова … заменить словами …» and the
  portal has already merged them into the base acts' redactions);
* ``all`` — everything above;
* a comma-separated nd list — exactly those documents.

Modes:

* ``--mode full``  — fetch every target act unconditionally.
* ``--mode update`` — fetch each act's wrapper (cheap, ~40-100КБ), compare its
  current rdk with the rdk already indexed in OpenSearch, and re-fetch only
  the acts whose redaction changed. New acts (no indexed rdk) are fetched.

Long runs: the ФЗ corpus is ~1000 substantive acts and takes hours, so the
snapshot is written incrementally and ``--resume`` skips acts already present
in the output zip; ``--shard i/N`` splits the target list deterministically so
a nightly job can cover the corpus over N runs (every act is still covered —
it is scheduling, not sampling).

Safety: the fetched document title is verified against the expected name (or
number) — a re-pointed/stale nd fails that act loudly instead of silently
ingesting the wrong document. Every failure is reported and the exit code is
non-zero if anything failed (no silent partial success).

Usage:
    uv run python scripts/scrape_normativka.py --out /tmp/normativka.zip
    uv run python scripts/scrape_normativka.py --acts fz --out /tmp/fz.zip --resume
    uv run python scripts/scrape_normativka.py --out /tmp/upd.zip \
        --mode update --opensearch-url http://1.2.3.4:9200
"""
import argparse
import hashlib
import json
import logging
import sys
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.normativka.acts import CODICES, normalize_ref  # noqa: E402
from app.normativka.catalog import (  # noqa: E402
    DOC_KIND_FKZ,
    DOC_KIND_FZ,
    DOC_KIND_ZAKON,
    enumerate_acts,
    is_technical_act,
)
from app.normativka.fetch import ActMeta, fetch_act_meta, fetch_act_text  # noqa: E402
from app.normativka.ips_client import IpsClient, IpsError  # noqa: E402
from app.normativka.parse import split_articles  # noqa: E402
from app.search.normativka_index import NORMATIVKA_INDEX_ALIAS  # noqa: E402

logger = logging.getLogger("scrape_normativka")

# Title sanity check: the first N normalized chars of the expected name must
# appear in the fetched wrapper title. Catches a re-pointed/stale nd without
# being brittle about punctuation. Normalization is shared with the runtime
# alias resolver (acts.normalize_ref) so the two rules cannot drift apart.
_TITLE_CHECK_CHARS = 20

_CATALOG_KINDS = {"fz": DOC_KIND_FZ, "fkz": DOC_KIND_FKZ, "zakony": DOC_KIND_ZAKON}


class NoArticlesError(IpsError):
    """The document carries no «Статья N» structure at all.

    Not a failure of the fetch or the parser: old short acts genuinely have no
    articles («О реорганизации Комитета РФ по оборонным отраслям
    промышленности», «Об учреждении юбилейной медали "50 лет Победы"»). They
    are reported and counted separately, because folding them into failures
    would make every large run red and teach everyone to ignore red runs.
    """


@dataclass(frozen=True)
class ScrapeTarget:
    """One act to fetch, from either the curated table or the live catalog."""

    nd: str
    kind: str
    name: str
    number: str = ""
    date: str = ""
    aliases: tuple[str, ...] = ()


def _title_matches(target: ScrapeTarget, fetched_title: str) -> bool:
    """Verify the fetched document is the expected one.

    Accepts a match on the name prefix OR on the act number, because the ИПС
    <title> carries the full name for some kinds and the «Вид от ДД.ММ.ГГГГ
    № N-ФЗ» reference for others. When the target carries NEITHER (an nd named
    explicitly on the command line) there is nothing to verify against — the
    caller asked for that exact document, so the fetched title is taken as the
    act's name; a non-empty title is all that is required.
    """
    title = normalize_ref(fetched_title)
    if not title:
        return False
    if not target.name and not target.number:
        return True
    if target.name and normalize_ref(target.name)[:_TITLE_CHECK_CHARS] in title:
        return True
    return bool(target.number) and normalize_ref(target.number) in title


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


def scrape_act(client: IpsClient, target: ScrapeTarget, *, meta: ActMeta | None = None) -> dict:
    """Fetch and parse one act; returns the per-act snapshot payload.

    ``meta`` lets update mode reuse the wrapper it already fetched for the rdk
    comparison instead of paying a second round-trip to a flaky portal.
    """
    if meta is None:
        meta = fetch_act_meta(client, target.nd)
    if not _title_matches(target, meta.title):
        raise IpsError(
            f"nd={target.nd}: заголовок «{meta.title}» не похож на «{target.name}» "
            f"(№ {target.number or '—'}) — nd указывает не на тот документ"
        )
    html = fetch_act_text(client, target.nd, rdk=meta.current_rdk)
    articles = split_articles(html)
    if not articles:
        raise NoArticlesError(f"nd={target.nd}: в документе нет статей («Статья N» не встречается)")

    # Document ids are sha(nd|номер статьи), so two articles sharing a number
    # inside one act OVERWRITE each other in the index — silent data loss. It
    # happens in compound documents (a law plus the code it enacts, each with
    # its own «Статья 1…»); such wrappers are filtered out by name, so a
    # remaining duplicate means something unexpected and must be visible.
    numbers = [article.number for article in articles]
    duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
    if duplicates:
        logger.warning(
            "nd=%s (%s): повторяющиеся номера статей %s — в индексе они перезапишут друг друга",
            target.nd, target.name[:50], duplicates[:10],
        )
    return {
        "act": {
            "nd": target.nd,
            "kind": target.kind,
            # Для каталожных актов имя из каталога, для кодексов — каноническое;
            # если ни того ни другого нет, берём заголовок документа.
            "name": target.name or meta.title,
            "aliases": list(target.aliases),
            "number": target.number,
            "date": target.date,
            "rdk": meta.current_rdk,
            "title_fetched": meta.title,
        },
        "articles": [asdict(article) for article in articles],
    }


def _shard_of(nd: str, shards: int) -> int:
    """Stable shard index for an nd (same act lands in the same shard always)."""
    return int(hashlib.sha256(nd.encode()).hexdigest(), 16) % shards


def build_targets(client: IpsClient, spec: str) -> list[ScrapeTarget]:
    """Resolve the ``--acts`` spec into concrete targets."""
    codices = {
        act.nd: ScrapeTarget(act.nd, act.kind, act.name, act.number, act.adoption_date, act.aliases)
        for act in CODICES
    }
    spec = spec.strip().lower()
    wanted_kinds: list[str] = []
    if spec == "all":
        wanted_kinds = list(_CATALOG_KINDS)
    elif spec in _CATALOG_KINDS:
        wanted_kinds = [spec]
    elif spec != "kodeksy":
        targets = []
        for nd in [p.strip() for p in spec.split(",") if p.strip()]:
            if nd in codices:
                targets.append(codices[nd])
            elif nd.isdigit():
                # Явно названный nd вне таблицы: имя возьмём из заголовка
                # документа, сверка пойдёт по нему же (пользователь его назвал).
                targets.append(ScrapeTarget(nd, "fz", ""))
            else:
                raise SystemExit(f"Непонятная цель: {nd!r} (ожидался nd, kodeksy, fz, fkz, zakony, all)")
        return targets

    targets = list(codices.values()) if spec in ("kodeksy", "all") else []
    for kind in wanted_kinds:
        entries = enumerate_acts(client, doc_kind_id=_CATALOG_KINDS[kind])
        substantive = [e for e in entries if not is_technical_act(e.full_name)]
        logger.info(
            "%s: в каталоге %d, содержательных %d (технических отброшено %d)",
            kind, len(entries), len(substantive), len(entries) - len(substantive),
        )
        skipped_noname = 0
        for entry in substantive:
            if not entry.full_name.strip():
                skipped_noname += 1
                continue
            targets.append(
                ScrapeTarget(entry.nd, entry.kind or kind, entry.full_name, entry.number, entry.date)
            )
        if skipped_noname:
            logger.warning("%s: без названия в каталоге и потому пропущено: %d", kind, skipped_noname)
    return targets


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape нормативка from pravo.gov.ru ИПС")
    parser.add_argument("--out", required=True, help="Path of the snapshot zip to write")
    parser.add_argument(
        "--acts",
        default="kodeksy",
        help="kodeksy | fz | fkz | zakony | all | список nd через запятую",
    )
    parser.add_argument("--mode", choices=["full", "update"], default="full")
    parser.add_argument("--opensearch-url", help="Нужен для --mode update (сравнение rdk)")
    parser.add_argument("--index-alias", default=NORMATIVKA_INDEX_ALIAS)
    parser.add_argument("--pause", type=float, default=1.5, help="Пауза между запросами к ИПС, сек")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Дописывать существующий зип, пропуская уже скачанные акты (для многочасовых прогонов)",
    )
    parser.add_argument(
        "--shard",
        help="i/N — взять только свою часть целей (детерминированно по nd). "
        "Каждый акт попадает ровно в один шард, за N прогонов покрывается весь корпус.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    already: set[str] = set()
    out_path = Path(args.out)
    if args.resume and out_path.exists():
        with zipfile.ZipFile(out_path) as existing:
            already = {
                Path(n).stem for n in existing.namelist() if n.startswith("acts/") and n.endswith(".json")
            }
        logger.info("Докачка: в %s уже есть %d актов", out_path.name, len(already))

    indexed_rdk: dict[str, str] = {}
    if args.mode == "update":
        if not args.opensearch_url:
            parser.error("--mode update требует --opensearch-url")
        indexed_rdk = _indexed_rdk_by_nd(args.opensearch_url, args.index_alias)

    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    written: list[dict] = []
    skipped: list[str] = []
    no_articles: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []

    with IpsClient(pause=args.pause) as client:
        targets = build_targets(client, args.acts)

        if args.shard:
            try:
                index, total = (int(p) for p in args.shard.split("/", 1))
            except ValueError:
                parser.error("--shard ожидает формат i/N, например 1/7")
            if not 1 <= index <= total:
                parser.error(f"--shard {args.shard}: i должно быть в диапазоне 1..N")
            before = len(targets)
            targets = [t for t in targets if _shard_of(t.nd, total) == index - 1]
            logger.info("Шард %d/%d: %d целей из %d", index, total, len(targets), before)

        if already:
            targets = [t for t in targets if t.nd not in already]
        logger.info("К обработке: %d актов", len(targets))

        mode = "a" if (args.resume and out_path.exists()) else "w"
        with zipfile.ZipFile(out_path, mode, compression=zipfile.ZIP_DEFLATED) as archive:
            for position, target in enumerate(targets, start=1):
                label = target.name or f"nd={target.nd}"
                try:
                    meta = None
                    if args.mode == "update":
                        meta = fetch_act_meta(client, target.nd)
                        if indexed_rdk.get(target.nd) == meta.current_rdk:
                            logger.info("%s: rdk=%s не изменился — пропуск", label[:60], meta.current_rdk)
                            skipped.append(target.nd)
                            continue
                    payload = scrape_act(client, target, meta=meta)
                    payload["act"]["fetched_at"] = fetched_at
                    archive.writestr(f"acts/{target.nd}.json", json.dumps(payload, ensure_ascii=False))
                    written.append(
                        {
                            "nd": target.nd,
                            "kind": target.kind,
                            "name": payload["act"]["name"],
                            "number": target.number,
                            "rdk": payload["act"]["rdk"],
                            "articles": len(payload["articles"]),
                        }
                    )
                    logger.info(
                        "[%d/%d] %s (%s): rdk=%s, статей=%d",
                        position, len(targets), label[:60], target.number or "—",
                        payload["act"]["rdk"], len(payload["articles"]),
                    )
                except NoArticlesError as exc:
                    logger.warning(
                        "[%d/%d] %s (nd=%s): без статей — не индексируем",
                        position, len(targets), label[:60], target.nd,
                    )
                    no_articles.append((target.nd, str(exc)))
                except IpsError as exc:
                    logger.error("[%d/%d] %s (nd=%s): %s", position, len(targets), label[:60], target.nd, exc)
                    failed.append((target.nd, str(exc)))

            # Манифест пишется последним и в режиме докачки заменяет прежний
            # (zip допускает дубли имён, читатель берёт последнюю запись).
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "fetched_at": fetched_at,
                        "mode": args.mode,
                        "acts_spec": args.acts,
                        "shard": args.shard or "",
                        "acts": written,
                        "no_articles": no_articles,
                        "failed": failed,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )

    logger.info(
        "Готово: %d актов записано, %d без изменений, %d без статей, %d с ошибками",
        len(written), len(skipped), len(no_articles), len(failed),
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
