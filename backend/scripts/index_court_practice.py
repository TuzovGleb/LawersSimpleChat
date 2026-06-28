#!/usr/bin/env python3
"""Bulk-index court practice JSON pages into OpenSearch."""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import zipfile
from pathlib import Path

# Week-paginated datasets (e.g. mos_gorsud) name pages "page-WWWNNN.json", where
# the first 3 digits are the scrape week and the last 3 are the page within it.
# Used by the optional --week-from/--week-to filter; the week is not in the JSON.
_WEEK_RE = re.compile(r"page-(\d{3})\d{3}\.json$")


def _file_week(path: Path) -> int | None:
    match = _WEEK_RE.search(path.name)
    return int(match.group(1)) if match else None

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from app.search.client import OpenSearchConfig, build_opensearch_client
from app.search.index import (
    INDEX_ALIAS,
    INDEX_VERSION,
    bulk_index_documents,
    delete_superseded_indices,
    ensure_index,
    normalize_case,
    parallel_index_documents,
    set_index_refresh,
)
from app.search.case_types import CASE_TYPE_CODE_TO_NAME, case_type_from_catalog
from app.search.regions import region_code_from_catalog

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def iter_cases_from_page(page: dict, region_code: int | None = None, case_type: str | None = None):
    page_meta = {
        "courtName": page.get("courtName"),
        "vnkod": page.get("vnkod"),
        # Dataset-level region from the catalog; normalize_case prefers it and
        # falls back to the court's vnkod prefix when it is None.
        "region_code": region_code,
        # Dataset-level вид судопроизводства (civil/criminal/...) from the catalog.
        "case_type": case_type,
    }
    for case in page.get("cases") or []:
        document = normalize_case(case, page_meta)
        if document:
            yield document


def _directory_region_code(directory: Path) -> int | None:
    """Resolve a dataset's region code from its _catalog.json (root first)."""
    root = directory / "_catalog.json"
    candidates = [root] if root.is_file() else sorted(directory.rglob("_catalog.json"))
    for path in candidates:
        try:
            with path.open(encoding="utf-8") as handle:
                catalog = json.load(handle)
        except (OSError, ValueError):
            continue
        code = region_code_from_catalog(catalog)
        if code is not None:
            return code
    return None


def _directory_case_type(directory: Path) -> str | None:
    """Resolve a dataset's case type from its _catalog.json (root first)."""
    root = directory / "_catalog.json"
    candidates = [root] if root.is_file() else sorted(directory.rglob("_catalog.json"))
    for path in candidates:
        try:
            with path.open(encoding="utf-8") as handle:
                catalog = json.load(handle)
        except (OSError, ValueError):
            continue
        code = case_type_from_catalog(catalog)
        if code is not None:
            return code
    return None


def _zip_case_type(archive: zipfile.ZipFile) -> str | None:
    for name in sorted(n for n in archive.namelist() if n.endswith("_catalog.json")):
        try:
            with archive.open(name) as handle:
                catalog = json.load(handle)
        except (KeyError, ValueError):
            continue
        code = case_type_from_catalog(catalog)
        if code is not None:
            return code
    return None


def _zip_region_code(archive: zipfile.ZipFile) -> int | None:
    for name in sorted(n for n in archive.namelist() if n.endswith("_catalog.json")):
        try:
            with archive.open(name) as handle:
                catalog = json.load(handle)
        except (KeyError, ValueError):
            continue
        code = region_code_from_catalog(catalog)
        if code is not None:
            return code
    return None


def iter_cases_from_zip(zip_path: Path, case_type: str | None = None):
    with zipfile.ZipFile(zip_path) as archive:
        region_code = _zip_region_code(archive)
        case_type = case_type or _zip_case_type(archive)
        json_names = sorted(name for name in archive.namelist() if name.endswith(".json") and not name.endswith("/"))
        logger.info(
            "Found %s JSON files in archive (region_code=%s case_type=%s)",
            len(json_names), region_code, case_type,
        )
        for name in json_names:
            with archive.open(name) as handle:
                page = json.load(handle)
            yield from iter_cases_from_page(page, region_code, case_type)


def iter_cases_from_directory(
    directory: Path,
    case_type: str | None = None,
    week_from: int | None = None,
    week_to: int | None = None,
):
    region_code = _directory_region_code(directory)
    case_type = case_type or _directory_case_type(directory)
    json_paths = sorted(path for path in directory.rglob("*.json") if path.is_file())
    if week_from is not None or week_to is not None:
        lo = week_from if week_from is not None else -1
        hi = week_to if week_to is not None else 10**9
        before = len(json_paths)
        # Keep only week-paginated pages within range; this also drops files that
        # carry no week (e.g. legacy page-0001.json), which is intended.
        json_paths = [p for p in json_paths if (w := _file_week(p)) is not None and lo <= w <= hi]
        logger.info("Week filter [%s..%s]: kept %s of %s files", week_from, week_to, len(json_paths), before)
    logger.info(
        "Found %s JSON files under %s (region_code=%s case_type=%s)",
        len(json_paths), directory, region_code, case_type,
    )
    for path in json_paths:
        with path.open(encoding="utf-8") as handle:
            page = json.load(handle)
        yield from iter_cases_from_page(page, region_code, case_type)


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
    parser.add_argument(
        "--case-type",
        choices=sorted(CASE_TYPE_CODE_TO_NAME),
        default=None,
        help="Override the dataset's вид судопроизводства. Normally auto-resolved "
        "from the catalog (deloFilter.delo_table / category); pass this only when "
        "the catalog lacks a marker.",
    )
    parser.add_argument(
        "--week-from",
        type=int,
        default=None,
        help="For week-paginated datasets (mos_gorsud, page-WWWNNN.json): index only "
        "files whose scrape week >= this. Files without a week are skipped while filtering.",
    )
    parser.add_argument(
        "--week-to",
        type=int,
        default=None,
        help="Upper bound (inclusive) for the --week-from filter.",
    )
    parser.add_argument("--skip-existing", action="store_true", help="Skip documents already present in the index")
    parser.add_argument(
        "--no-refresh-tune",
        action="store_true",
        help="Do not disable refresh during load (refresh is normally turned off, then restored)",
    )
    parser.add_argument(
        "--delete-old",
        action="store_true",
        help="After a fully successful load, delete prior indices of this family no longer bound to the alias",
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
        case_iter = iter_cases_from_directory(
            source_path, case_type=args.case_type, week_from=args.week_from, week_to=args.week_to
        )
    else:
        if args.week_from is not None or args.week_to is not None:
            logger.warning("--week-from/--week-to is only applied to directory sources; ignoring for zip")
        case_iter = iter_cases_from_zip(source_path, case_type=args.case_type)

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

    if args.delete_old:
        if errors:
            logger.warning(
                "Skipping --delete-old: %s indexing error(s); keeping previous indices as fallback",
                errors,
            )
        else:
            removed = delete_superseded_indices(client, index_name=args.index_name, alias=INDEX_ALIAS)
            logger.info("Deleted superseded indices: %s", removed or "none")

    logger.info("Done. indexed=%s skipped=%s errors=%s", indexed, skipped, errors)
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
