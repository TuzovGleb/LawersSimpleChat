"""OpenSearch index for нормативка (statutes): mapping and document normalization.

One indexed document = one article of one act, in its CURRENT redaction. The
``rdk`` field records which ИПС redaction the text came from — the update
scraper compares it with the wrapper's current rdk to decide whether an act
needs re-ingesting, and the indexer uses it to purge articles left over from
a superseded redaction.

Lives next to (not inside) the court-practice index module: same OpenSearch
instance, same analyzer, different corpus and lifecycle.
"""
import copy
import hashlib
import re

from app.normativka.acts import aliases_for
from app.search.index import INDEX_BODY as _COURT_INDEX_BODY

# v2 adds the searchable act_number subfield: lawyers cite federal laws by
# number («44-ФЗ», «152-ФЗ»), so it must be both an exact term (resolution)
# and analyzed text (topical search). Mapping change ⇒ new version + full
# reload + alias swap, per the court-practice convention.
NORMATIVKA_INDEX_VERSION = "legal_acts_v2"
NORMATIVKA_INDEX_ALIAS = "legal_acts"

NORMATIVKA_INDEX_BODY = {
    # Same single-shard settings and the same custom russian analyzer as the
    # court-practice index — one cluster, one analysis convention. Deep-copied,
    # not referenced: a court-motivated analyzer change (with its own index
    # version bump) must not silently redefine what legal_acts_v1 means.
    "settings": copy.deepcopy(_COURT_INDEX_BODY["settings"]),
    "mappings": {
        "properties": {
            "article_id": {"type": "keyword"},
            "act_nd": {"type": "keyword"},
            "act_kind": {"type": "keyword"},  # kodeks | fz | zakon_rf
            "act_name": {
                "type": "text",
                "analyzer": "russian",
                "fields": {"raw": {"type": "keyword"}},
            },
            "act_aliases": {"type": "text", "analyzer": "russian"},
            # keyword for exact resolution («44-ФЗ» → тот самый закон),
            # .text for topical queries that mention the number in passing.
            "act_number": {
                "type": "keyword",
                "fields": {"text": {"type": "text", "analyzer": "russian"}},
            },
            "act_date": {"type": "date", "format": "yyyy-MM-dd||strict_date_optional_time"},
            "article_number": {"type": "keyword"},
            "article_title": {"type": "text", "analyzer": "russian"},
            "article_text": {"type": "text", "analyzer": "russian"},
            "chapter_path": {"type": "text", "analyzer": "russian"},
            "rdk": {"type": "keyword"},
            "source_url": {"type": "keyword", "index": False},
            "indexed_at": {"type": "date", "format": "yyyy-MM-dd||strict_date_optional_time"},
        }
    },
}


def generate_article_id(act_nd: str, article_number: str) -> str:
    """Stable id: re-indexing the same article of the same act overwrites it."""
    payload = f"{act_nd}|{article_number}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def normalize_act_number(value: str) -> str:
    """Canonical act number for term lookups: «№ 44 ФЗ», «44фз» -> «44-ФЗ».

    Lawyers write federal-law numbers every possible way; the index stores the
    ИПС form («44-ФЗ», «2300-I»), so every inbound variant is folded to it.
    Returns '' when the value carries no number at all.
    """
    cleaned = (value or "").strip().lstrip("№").strip()
    match = re.match(r"^(\d+)\s*[-–_/ ]?\s*(фз|фкз|ф\.з\.|i+|[ivxlc]+)?\.?$", cleaned, re.I)
    if not match:
        return ""
    number, suffix = match.group(1), (match.group(2) or "").lower()
    if not suffix:
        return number
    if suffix.startswith("фкз"):
        return f"{number}-ФКЗ"
    if suffix.startswith("ф"):
        return f"{number}-ФЗ"
    return f"{number}-{suffix.upper()}"  # римские: «2300-I»


def normalize_article_number(value: str) -> str:
    """Canonical dotted citation form for lookups: «333.34-1» -> «333.34.1».

    Mirrors the scraper-side superscript normalization so a term query built
    from a lawyer's citation always matches the indexed key. Strips the
    leading «ст.»/«статья» decorations tools may receive.
    """
    cleaned = value.strip().lower()
    for prefix in ("статья", "ст.", "ст"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            break
    return cleaned.replace(" ", "").replace("-", ".")


def normalize_article(
    article: dict,
    *,
    act: dict,
    indexed_at: str,
) -> dict | None:
    """Build an indexable document from a parsed article + act metadata.

    ``article``: number/title/text/chapter_path (see app.normativka.parse).
    ``act``: nd/kind/name/aliases/number/date/rdk — the scraper's manifest entry.
    """
    number = (article.get("number") or "").strip()
    text = (article.get("text") or "").strip()
    act_nd = (act.get("nd") or "").strip()
    if not number or not text or not act_nd:
        return None

    article_id = generate_article_id(act_nd, number)
    # Аббревиатуры юристов («об ООО», «ОСАГО», «ЗоЗПП») лексически не совпадают
    # с официальными названиями, поэтому подмешиваются из курируемой таблицы —
    # без них такой запрос не найдёт акт вообще.
    aliases = tuple(act.get("aliases") or ()) + aliases_for(
        act.get("number") or "", act.get("name") or ""
    )
    return {
        "_id": article_id,
        "article_id": article_id,
        "act_nd": act_nd,
        "act_kind": act.get("kind") or "fz",
        "act_name": act.get("name") or "",
        "act_aliases": ", ".join(dict.fromkeys(aliases)),
        "act_number": act.get("number") or "",
        "act_date": act.get("date") or None,
        "article_number": number,
        "article_title": article.get("title") or "",
        "article_text": text,
        "chapter_path": article.get("chapter_path") or "",
        "rdk": act.get("rdk") or "",
        # Official document card on the portal — the citation link shown to lawyers.
        "source_url": f"http://pravo.gov.ru/proxy/ips/?docbody=&nd={act_nd}",
        "indexed_at": indexed_at,
    }
