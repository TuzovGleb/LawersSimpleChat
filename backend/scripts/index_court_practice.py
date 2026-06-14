#!/usr/bin/env python3
"""Bulk-index court practice JSON pages into OpenSearch."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import zipfile
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from app.search.client import OpenSearchConfig, build_opensearch_client
from app.search.index import (
    INDEX_VERSION,
    bulk_index_documents,
    ensure_index,
    normalize_case,
    parallel_index_documents,
    set_index_refresh,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def iter_cases_from_page(page: dict):
    page_meta = {
        "courtName": page.get("courtName"),
        "vnkod": page.get("vnkod"),
    }
    for case in page.get("cases") or []:
        document = normalize_case(case, page_meta)
        if document:
            yield document


def iter_cases_from_zip(zip_path: Path):
    with zipfile.ZipFile(zip_path) as archive:
        json_names = sorted(name for name in archive.namelist() if name.endswith(".json") and not name.endswith("/"))
        logger.info("Found %s JSON files in archive", len(json_names))
        for name in json_names:
            with archive.open(name) as handle:
                page = json.load(handle)
            yield from iter_cases_from_page(page)


def iter_cases_from_directory(directory: Path):
    json_paths = sorted(path for path in directory.rglob("*.json") if path.is_file())
    logger.info("Found %s JSON files under %s", len(json_paths), directory)
    for path in json_paths:
        with path.open(encoding="utf-8") as handle:
            page = json.load(handle)
        yield from iter_cases_from_page(page)


def load_existing_ids(client, index_name: str) -> set[str]:
    if not client.indices.exists(index=index_name):
        return set()
    ids: set[str] = set()
    response = client.search(
        index=index_name,
        body={"query": {"match_all": {}}, "_source": False, "size": 10000},
        scroll="2m",
    )
    scroll_id = response.get("_scroll_id")
    hits = response.get("hits", {}).get("hits", [])
    while hits:
        ids.update(hit["_id"] for hit in hits)
        if not scroll_id:
            break
        response = client.scroll(scroll_id=scroll_id, scroll="2m")
        scroll_id = response.get("_scroll_id")
        hits = response.get("hits", {}).get("hits", [])
    if scroll_id:
        client.clear_scroll(scroll_id=scroll_id)
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description="Index court practice sample into OpenSearch")
    parser.add_argument(
        "--source",
        required=True,
        help="Path to zip archive or directory (all *.json files are scanned recursively)",
    )
    parser.add_argument("--opensearch-url", default="http://localhost:9200")
    parser.add_argument("--index-name", default=INDEX_VERSION)
    parser.add_argument("--batch-size", type=int, default=500, help="Documents per bulk request")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Concurrent bulk requests. >1 streams via parallel_bulk; 2-4 suits a small VM.",
    )
    parser.add_argument("--skip-existing", action="store_true", help="Skip documents already present in the index")
    parser.add_argument(
        "--no-refresh-tune",
        action="store_true",
        help="Do not disable refresh during load (refresh is normally turned off, then restored)",
    )
    args = parser.parse_args()

    source_path = Path(args.source)
    if not source_path.exists():
        logger.error("Source path does not exist: %s", source_path)
        return 1

    config = OpenSearchConfig(url=args.opensearch_url)
    client = build_opensearch_client(config)
    ensure_index(client, index_name=args.index_name)

    existing_ids: set[str] = set()
    if args.skip_existing:
        logger.info("Loading existing document ids...")
        existing_ids = load_existing_ids(client, args.index_name)
        logger.info("Found %s existing documents", len(existing_ids))

    if source_path.is_dir():
        case_iter = iter_cases_from_directory(source_path)
    else:
        case_iter = iter_cases_from_zip(source_path)

    indexed = 0
    skipped = 0
    errors = 0

    def documents():
        nonlocal skipped
        for document in case_iter:
            if document["_id"] in existing_ids:
                skipped += 1
                continue
            yield document

    tune_refresh = not args.no_refresh_tune
    if tune_refresh:
        logger.info("Disabling refresh for bulk load")
        set_index_refresh(client, index_name=args.index_name, interval="-1")

    try:
        if args.workers > 1:
            logger.info("Parallel load: workers=%s chunk_size=%s", args.workers, args.batch_size)
            for ok, info in parallel_index_documents(
                client,
                documents(),
                index_name=args.index_name,
                chunk_size=args.batch_size,
                thread_count=args.workers,
            ):
                if ok:
                    indexed += 1
                else:
                    errors += 1
                    if errors <= 5:
                        logger.warning("Index failure: %s", info)
                if (indexed + errors) % 5000 == 0:
                    logger.info("Progress: indexed=%s errors=%s", indexed, errors)
        else:
            batch: list[dict] = []
            for document in documents():
                batch.append(document)
                if len(batch) < args.batch_size:
                    continue
                success, failed = bulk_index_documents(client, batch, index_name=args.index_name)
                indexed += success
                errors += len(failed)
                logger.info("Indexed batch: success=%s failed=%s total=%s", success, len(failed), indexed)
                batch.clear()
            if batch:
                success, failed = bulk_index_documents(client, batch, index_name=args.index_name)
                indexed += success
                errors += len(failed)
                logger.info("Indexed final batch: success=%s failed=%s", success, len(failed))
    finally:
        if tune_refresh:
            logger.info("Restoring refresh interval")
            set_index_refresh(client, index_name=args.index_name, interval="1s")
            client.indices.refresh(index=args.index_name)

    logger.info("Done. indexed=%s skipped=%s errors=%s", indexed, skipped, errors)
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
