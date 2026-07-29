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

from app.search.index import INDEX_BODY as _COURT_INDEX_BODY

NORMATIVKA_INDEX_VERSION = "legal_acts_v1"
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
            "act_number": {"type": "keyword"},
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
    return {
        "_id": article_id,
        "article_id": article_id,
        "act_nd": act_nd,
        "act_kind": act.get("kind") or "fz",
        "act_name": act.get("name") or "",
        "act_aliases": ", ".join(act.get("aliases") or ()),
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
