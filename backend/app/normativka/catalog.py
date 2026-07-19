"""Attribute-search enumeration of the ИПС corpus (the «опись склада»).

One catalog query = «дай все документы вида X в действующем статусе с
текстом». This is the ingest-side discovery mechanism; runtime never touches
it. Result rows are parsed PER BLOCK (``class="list_elem``): a global regex
over the page pairs each nd with the NEIGHBOURING row's title, because every
row's footer icons repeat its nd right before the next row's anchor (verified
live — naive parsing shifted all titles by one).
"""
import logging
import re
from dataclasses import dataclass

from app.normativka.ips_client import IpsClient, build_query

logger = logging.getLogger(__name__)

# Classifier ids (вид документа / статус действия) from the ИПС dictionary.
DOC_KIND_KODEKS = "102000486"
STATUS_ACTIVE_AMENDED = "102000038"     # «Действует с изменениями»
STATUS_ACTIVE_UNCHANGED = "102000037"   # «Действует без изменений»

PAGE_SIZE = 20  # the server's default; start=N pages through the result set

_BLOCK_RE = re.compile(r'class="list_elem[^"]*"', re.I)
_ND_RE = re.compile(r"nd=(\d+)")
_TITLE_RE = re.compile(r'class="bold"[^>]*>\s*([^<]{5,200}?)\s*</a>', re.S)
_FULLNAME_RE = re.compile(r'<span class="bold">([^<]{3,300})</span>', re.S)
_STATUS_RE = re.compile(r'class="tiny_italic_bold">\s*([^<]{3,60}?)\s*<', re.S)


@dataclass(frozen=True)
class CatalogEntry:
    nd: str
    short_ref: str   # «Кодекс Российской Федерации от 30.12.2001 № 197-ФЗ»
    full_name: str   # «Трудовой кодекс Российской Федерации»
    status: str      # «Действует с изменениями»


def _parse_page(html: str) -> list[CatalogEntry]:
    entries: list[CatalogEntry] = []
    blocks = _BLOCK_RE.split(html)[1:]  # text before the first row is chrome
    for block in blocks:
        nd_match = _ND_RE.search(block)
        title_match = _TITLE_RE.search(block)
        if not nd_match or not title_match:
            continue
        fullname_match = _FULLNAME_RE.search(block, title_match.end())
        status_match = _STATUS_RE.search(block)
        status = status_match.group(1).strip() if status_match else ""
        entries.append(
            CatalogEntry(
                nd=nd_match.group(1),
                short_ref=" ".join(title_match.group(1).split()),
                full_name=" ".join(fullname_match.group(1).split()) if fullname_match else "",
                status=status,
            )
        )
    return entries


def enumerate_acts(
    client: IpsClient,
    *,
    doc_kind_id: str,
    status_ids: tuple[str, ...] = (STATUS_ACTIVE_AMENDED, STATUS_ACTIVE_UNCHANGED),
    max_pages: int = 1000,
) -> list[CatalogEntry]:
    """List every document of a kind in the given statuses, with text present.

    Pages through ``start=N`` until a page adds nothing new. That is the ONLY
    stop condition: stopping early on a short page would let a single
    unparseable row (odd title markup) silently truncate the whole
    enumeration. ``max_pages`` is a runaway guard for the ИПС echoing the same
    page regardless of offset (its known failure mode), not a result cap —
    1000 pages covers the largest corpus (все ФЗ) several times over.
    """
    overrides = {"a3": doc_kind_id, "a4": ";".join(status_ids)}
    seen: dict[str, CatalogEntry] = {}
    for page in range(max_pages):
        extra = {"sort": "7"}
        if page:
            extra["start"] = str(page * PAGE_SIZE)
        query = build_query("list_itself", overrides, extra=extra)
        html = client.get_text(query, min_bytes=2000)
        fresh = [e for e in _parse_page(html) if e.nd not in seen]
        if not fresh:
            break
        for entry in fresh:
            seen[entry.nd] = entry
    logger.info("Каталог перечислен", extra={"doc_kind": doc_kind_id, "count": len(seen)})
    return list(seen.values())
