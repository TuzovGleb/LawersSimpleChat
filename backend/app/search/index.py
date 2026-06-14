"""OpenSearch index mapping and document normalization."""
from datetime import datetime
import hashlib
import logging

from typing import Iterable, Iterator

from opensearchpy import OpenSearch
from opensearchpy.helpers import bulk, parallel_bulk

logger = logging.getLogger(__name__)

INDEX_VERSION = "court_decisions_v2"
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


def generate_decision_id(vnkod: str, case_number: str) -> str:
    """Stable document id from court code + case number (works without court UID)."""
    normalized_vnkod = (vnkod or "").strip()
    normalized_case_number = (case_number or "").strip()
    if not normalized_case_number:
        return ""
    payload = f"{normalized_vnkod}|{normalized_case_number}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def normalize_case(case: dict, page_meta: dict) -> dict | None:
    case_number = case.get("caseNumber")
    act_text = case.get("actText")
    if not case_number or not isinstance(case_number, str) or not case_number.strip():
        return None
    if not isinstance(act_text, str) or not act_text.strip():
        return None

    vnkod = page_meta.get("vnkod") or case.get("vnkod") or ""
    decision_id = generate_decision_id(vnkod, case_number)
    if not decision_id:
        return None

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

    return {
        "_id": decision_id,
        "decision_id": decision_id,
        "court_uid": court_uid,
        "case_number": case_number.strip(),
        "case_number_text": case_number.strip(),
        "act_title": case.get("actTitle") or "",
        "act_text": act_text,
        "category": category_text,
        "participants_names": ", ".join(participant_names),
        "judge": case.get("judge") or "",
        "court_name": page_meta.get("courtName") or "",
        "vnkod": page_meta.get("vnkod") or case.get("vnkod") or "",
        "result_type": case.get("resultType") or "",
        "decision_result": case.get("decisionResult") or "",
        "filing_date": parse_russian_date(case.get("filingDate")),
        "decision_date": parse_russian_date(case.get("decisionDate")),
        "act_url": case.get("actUrl") or "",
        "case_details_url": case.get("caseDetailsUrl") or "",
    }


def ensure_index(client: OpenSearch, *, index_name: str = INDEX_VERSION, alias: str = INDEX_ALIAS) -> None:
    if not client.indices.exists(index=index_name):
        client.indices.create(index=index_name, body=INDEX_BODY)
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
