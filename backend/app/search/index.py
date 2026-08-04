"""OpenSearch index mapping and document normalization."""
from datetime import datetime
import hashlib
import re
import logging

from typing import Iterable, Iterator

from opensearchpy import OpenSearch
from opensearchpy.helpers import bulk, parallel_bulk

from app.search.courts import court_code_from_name

logger = logging.getLogger(__name__)

INDEX_VERSION = "court_decisions_v3"
INDEX_ALIAS = "court_decisions"

INDEX_BODY = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "analyzer": {
                "russian": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "russian_stemmer", "russian_stop"],
                }
            },
            "filter": {
                "russian_stemmer": {"type": "stemmer", "language": "russian"},
                "russian_stop": {"type": "stop", "stopwords": "_russian_"},
            },
        },
    },
    "mappings": {
        "properties": {
            "decision_id": {"type": "keyword"},
            "court_uid": {"type": "keyword"},
            "case_number": {"type": "keyword"},
            "case_number_text": {"type": "text", "analyzer": "russian"},
            "act_title": {"type": "text", "analyzer": "russian"},
            "act_text": {"type": "text", "analyzer": "russian"},
            "category": {"type": "text", "analyzer": "russian"},
            "participants_names": {"type": "text", "analyzer": "russian"},
            "judge": {"type": "keyword"},
            "court_name": {"type": "keyword"},
            "vnkod": {"type": "keyword"},
            "result_type": {"type": "keyword"},
            "decision_result": {"type": "text", "analyzer": "russian"},
            "filing_date": {"type": "date", "format": "yyyy-MM-dd||strict_date_optional_time||epoch_millis"},
            "decision_date": {"type": "date", "format": "yyyy-MM-dd||strict_date_optional_time||epoch_millis"},
            "act_url": {"type": "keyword", "index": False},
            "case_details_url": {"type": "keyword", "index": False},
            "region_code": {"type": "integer"},
            # Вид судопроизводства (civil/criminal/...): dataset-level, resolved
            # from the catalog at index time. See app.search.case_types.
            "case_type": {"type": "keyword"},
            # Short code of a notable higher court (ВС/КСОЮ/арбитраж), derived
            # from court_name. None for ordinary courts. See app.search.courts.
            "court_code": {"type": "keyword"},
            # Instance level: 1 first / 2 appeal / 3 cassation / 4 supreme. Used
            # to rank higher-court practice up. See derive_court_level.
            "court_level": {"type": "integer"},
        }
    },
}


def parse_russian_date(value: str | None) -> str | None:
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# СУДРФ court UID: "77RS0003-02-2026-004787-60" — two-digit region, two UPPERCASE
# letters, four digits. Matching the shape (not just "starts with two digits") is
# what keeps arbitration GUIDs out: "05da1c83-3a9b-…" also starts with two digits
# and would otherwise be read as region 05.
_COURT_UID_RE = re.compile(r"^(\d{2})[A-Z]{2}\d{4}-")


# СУДРФ court UID anywhere in the act text: КСОЮ acts quote the id of the case as
# it ran in the first instance ("91MS0013-01-2022-002261-28"), which is the only
# per-case region signal for the ~64% of cassation cases whose own `uid` is empty.
_UID_IN_TEXT_RE = re.compile(r"\b(\d{2})[A-ZА-Я]{2}\d{4}-\d{2}-\d{4}-\d{6}-\d{2}\b")


def _origin_region(case: dict, act_text: str) -> int | None:
    """Region where the case ORIGINATED, from per-case signals only.

    Used for multi-region corpora (catalog subj=0): КСОЮ and the arbitration
    appellate/okrug courts each span many subjects, so the region cannot come from
    the court itself. Deliberately returns None when no per-case signal exists —
    the page-level vnkod must NOT be used as a fallback here: it is the dominant
    court across 25 cases and stamped 57% of the КСОЮ cases with a foreign region.
    No region at all merely misses a filter; a wrong region returns wrong practice.
    """
    match = _COURT_UID_RE.match(case.get("uid") or "")
    if match:
        return int(match.group(1))
    match = _UID_IN_TEXT_RE.search(act_text or "")
    if match:
        return int(match.group(1))
    # Per-case code (arbitration: the converter derives it from the case number,
    # whose prefix names the first-instance court). Page-level vnkod is skipped.
    case_vnkod = (case.get("vnkod") or "")[:2]
    return int(case_vnkod) if case_vnkod.isdigit() else None


# Instance-level codes embedded in the СУДРФ / arbitration case number — the
# strongest per-case signal for court_level; court_name confirms the top tiers.
_CASSATION_PREFIXES = frozenset({"8Г", "8У", "8а", "77", "7У", "44У", "88"})
_APPEAL_PREFIXES = frozenset({"33", "33а", "22", "22К"})


def _case_number_prefix(case_number: str) -> str:
    cn = (case_number or "").strip()
    return cn.split("-", 1)[0] if "-" in cn else cn[:3]


def derive_court_level(
    case_number: str, court_name: str | None, act_title: str | None, region_code: int | None
) -> int:
    """Instance level: 1 first / 2 appeal / 3 cassation / 4 supreme.

    court_name pins the top tiers unambiguously (a КСОЮ / ВС РФ decision is
    always that level); for the rest the СУДРФ case-number prefix names the
    instance ("33-" civil appeal, "8Г-" cassation, "2-/1-" first). Moscow's
    criminal "10-" mixes first-instance materials with Мосгорсуд appeal, split by
    the act title. Falls through to 1 (first instance), the safe default.
    """
    name = court_name or ""
    if "Верховный Суд Российской Федерации" in name:
        return 4
    if "кассационный суд" in name or ("Арбитражный суд" in name and "округа" in name):
        return 3
    if region_code == 99:
        return 4
    prefix = _case_number_prefix(case_number)
    if prefix in _CASSATION_PREFIXES:
        return 3
    if "арбитражный апелляционный" in name.lower():
        return 2
    if prefix in _APPEAL_PREFIXES:
        return 2
    if prefix == "10" and "апелляц" in (act_title or "").lower():
        return 2
    return 1


def generate_decision_id(vnkod: str, case_number: str, doc_id: str | None = None) -> str:
    """Stable document id from court code + case number (works without court UID).

    ``doc_id`` disambiguates several acts of the SAME court on the SAME case — the
    arbitration corpus needs it: a case number runs through all instances there and
    a court can rule on one case repeatedly (239 such acts out of 42506 in the
    Нижегородская dataset), so court+case alone would silently overwrite them.
    СУДРФ pages carry no ``docId``, so their ids stay byte-for-byte the same and no
    reindex is needed.
    """
    normalized_vnkod = (vnkod or "").strip()
    normalized_case_number = (case_number or "").strip()
    if not normalized_case_number:
        return ""
    payload = f"{normalized_vnkod}|{normalized_case_number}"
    normalized_doc_id = (doc_id or "").strip()
    if normalized_doc_id:
        payload = f"{payload}|{normalized_doc_id}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def normalize_case(case: dict, page_meta: dict) -> dict | None:
    case_number = case.get("caseNumber")
    act_text = case.get("actText")
    if not case_number or not isinstance(case_number, str) or not case_number.strip():
        return None
    if not isinstance(act_text, str) or not act_text.strip():
        return None

    vnkod = page_meta.get("vnkod") or case.get("vnkod") or ""
    decision_id = generate_decision_id(vnkod, case_number, case.get("docId"))
    if not decision_id:
        return None

    # Numeric region key for filtering. Prefer the dataset-level code resolved
    # from the catalog (authoritative for the whole dataset: covers courts with
    # no vnkod such as областные суды, and legacy district prefixes like
    # Таймыр/Эвенкия that belong to their край). Fall back to the first two
    # chars of the court code, e.g. "52RS0001" -> 52. None when neither is
    # available, so it just won't match a region filter rather than mapping to
    # the wrong region.
    region_code = page_meta.get("region_code")
    if region_code is None:
        # Multi-region corpus (catalog subj=0): КСОЮ and the arbitration
        # appellate/okrug courts each cover many subjects, so the region must come
        # from THIS case, not from the page. The case's own uid carries the court
        # where the case originated ("place of birth"). The page-level vnkod is the
        # dominant prefix across 25 cases and would stamp the whole page with one
        # region — measured on the КСОЮ corpus, that mislabelled 57% of the cases
        # whose origin is known. Fall back to the page vnkod only when the case
        # carries no usable code of its own.
        region_code = _origin_region(case, act_text)

    participants = case.get("participants") or []
    participant_names = [
        p.get("name")
        for p in participants
        if isinstance(p, dict) and isinstance(p.get("name"), str) and p.get("name").strip()
    ]
    category = case.get("category") or []
    category_text = " > ".join(c for c in category if isinstance(c, str) and c.strip())

    court_uid = case.get("uid")
    if isinstance(court_uid, str):
        court_uid = court_uid.strip() or None
    else:
        court_uid = None

    court_name = page_meta.get("courtName") or ""
    act_title = case.get("actTitle") or ""

    return {
        "_id": decision_id,
        "decision_id": decision_id,
        "court_uid": court_uid,
        "case_number": case_number.strip(),
        "case_number_text": case_number.strip(),
        "act_title": act_title,
        "act_text": act_text,
        "category": category_text,
        "participants_names": ", ".join(participant_names),
        "judge": case.get("judge") or "",
        "court_name": court_name,
        "vnkod": page_meta.get("vnkod") or case.get("vnkod") or "",
        "result_type": case.get("resultType") or "",
        "decision_result": case.get("decisionResult") or "",
        "filing_date": parse_russian_date(case.get("filingDate")),
        "decision_date": parse_russian_date(case.get("decisionDate")),
        "act_url": case.get("actUrl") or "",
        "case_details_url": case.get("caseDetailsUrl") or "",
        "region_code": region_code,
        # Dataset-level вид судопроизводства, resolved from the catalog by the
        # indexer and threaded through page_meta. None when the catalog gives no
        # marker (then it just won't match a case_type filter).
        "case_type": page_meta.get("case_type"),
        # Short code of a notable higher court, from court_name (None otherwise).
        "court_code": court_code_from_name(court_name),
        # Instance level (1..4) for ranking higher-court practice up.
        "court_level": derive_court_level(case_number, court_name, act_title, region_code),
    }


def ensure_index(
    client: OpenSearch,
    *,
    index_name: str = INDEX_VERSION,
    alias: str = INDEX_ALIAS,
    body: dict | None = None,
) -> None:
    # The body default is the COURT mapping; creating another corpus's index
    # with it would silently break that corpus's term fields. Fail loudly
    # instead: any non-court alias must bring its own body.
    if body is None and alias != INDEX_ALIAS:
        raise ValueError(
            f"ensure_index: alias {alias!r} is not the court family — pass its index body explicitly"
        )
    if not client.indices.exists(index=index_name):
        client.indices.create(index=index_name, body=body or INDEX_BODY)
        logger.info("Created index", extra={"index": index_name})

    if client.indices.exists_alias(name=alias):
        bound_indices = list(client.indices.get_alias(name=alias).keys())
    else:
        bound_indices = []

    actions = [
        {"remove": {"index": bound_index, "alias": alias}}
        for bound_index in bound_indices
        if bound_index != index_name
    ]
    if index_name not in bound_indices:
        actions.append({"add": {"index": index_name, "alias": alias}})

    if actions:
        client.indices.update_aliases(body={"actions": actions})

    logger.info("Index alias ready", extra={"index": index_name, "alias": alias})


def delete_superseded_indices(
    client: OpenSearch, *, index_name: str = INDEX_VERSION, alias: str = INDEX_ALIAS
) -> list[str]:
    """Delete prior indices of this family that the alias no longer serves.

    Only touches versioned indices named ``{alias}_*``; never deletes the
    just-loaded ``index_name`` nor any index still bound to ``alias``. Returns
    the names that were deleted (empty list when there is nothing to clean up).
    """
    if client.indices.exists_alias(name=alias):
        bound = set(client.indices.get_alias(name=alias).keys())
    else:
        bound = set()

    family = client.indices.get(index=f"{alias}_*", ignore_unavailable=True, allow_no_indices=True)
    to_delete = [name for name in family if name != index_name and name not in bound]
    for name in to_delete:
        client.indices.delete(index=name)
        logger.info("Deleted superseded index", extra={"index": name})
    return to_delete


def _to_action(doc: dict, index_name: str) -> dict:
    return {"_index": index_name, "_id": doc["_id"], "_source": {k: v for k, v in doc.items() if k != "_id"}}


def bulk_index_documents(
    client: OpenSearch,
    documents: list[dict],
    *,
    index_name: str = INDEX_VERSION,
) -> tuple[int, list]:
    if not documents:
        return 0, []

    actions = [_to_action(doc, index_name) for doc in documents]
    return bulk(client, actions, raise_on_error=False, request_timeout=120)


def parallel_index_documents(
    client: OpenSearch,
    documents: Iterable[dict],
    *,
    index_name: str = INDEX_VERSION,
    chunk_size: int = 500,
    thread_count: int = 4,
    queue_size: int = 4,
) -> Iterator[tuple[bool, dict]]:
    """Stream documents through concurrent bulk requests.

    Yields (ok, info) per document as results arrive, so the caller can track
    progress and collect failures. Memory stays bounded (~queue_size*chunk_size
    actions in flight), so the source can be a lazy generator over 100k+ docs.
    """
    actions = (_to_action(doc, index_name) for doc in documents)
    yield from parallel_bulk(
        client,
        actions,
        chunk_size=chunk_size,
        thread_count=thread_count,
        queue_size=queue_size,
        raise_on_error=False,
        raise_on_exception=False,
        request_timeout=120,
    )


def set_index_refresh(client: OpenSearch, *, index_name: str = INDEX_VERSION, interval: str | None) -> None:
    """Tune refresh_interval for bulk load. Pass interval='-1' to disable, '1s' to restore."""
    client.indices.put_settings(index=index_name, body={"index": {"refresh_interval": interval}})
