"""OpenSearch index mapping and document normalization."""
from datetime import datetime
import logging

from opensearchpy import OpenSearch
from opensearchpy.helpers import bulk

logger = logging.getLogger(__name__)

INDEX_VERSION = "court_decisions_v1"
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
                    "filter": ["lowercase", "russian_morphology", "russian_stop"],
                }
            },
            "filter": {
                "russian_morphology": {"type": "stemmer", "language": "russian"},
                "russian_stop": {"type": "stop", "stopwords": "_russian_"},
            },
        },
    },
    "mappings": {
        "properties": {
            "uid": {"type": "keyword"},
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


def normalize_case(case: dict, page_meta: dict) -> dict | None:
    uid = case.get("uid")
    act_text = case.get("actText")
    if not uid or not isinstance(act_text, str) or not act_text.strip():
        return None

    participants = case.get("participants") or []
    participant_names = [
        p.get("name")
        for p in participants
        if isinstance(p, dict) and isinstance(p.get("name"), str) and p.get("name").strip()
    ]
    category = case.get("category") or []
    category_text = " > ".join(c for c in category if isinstance(c, str) and c.strip())

    return {
        "_id": uid,
        "uid": uid,
        "case_number": case.get("caseNumber") or "",
        "case_number_text": case.get("caseNumber") or "",
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
    if client.indices.exists(index=index_name):
        logger.info("Index already exists", extra={"index": index_name})
        return

    client.indices.create(index=index_name, body=INDEX_BODY)
    client.indices.put_alias(index=index_name, name=alias)
    logger.info("Created index and alias", extra={"index": index_name, "alias": alias})


def bulk_index_documents(
    client: OpenSearch,
    documents: list[dict],
    *,
    index_name: str = INDEX_VERSION,
) -> tuple[int, list]:
    if not documents:
        return 0, []

    actions = [{"_index": index_name, "_id": doc["_id"], "_source": {k: v for k, v in doc.items() if k != "_id"}} for doc in documents]
    return bulk(client, actions, raise_on_error=False, request_timeout=120)
