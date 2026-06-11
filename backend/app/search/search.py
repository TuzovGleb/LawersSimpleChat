"""Court practice search queries and result formatting."""
import asyncio
import logging
from typing import Any

from opensearchpy import OpenSearch

from app.search.client import OpenSearchConfig
from app.search.rrf import RankedDocument, reciprocal_rank_fusion

logger = logging.getLogger(__name__)

SEARCH_FIELDS = [
    "act_text^3",
    "category^2",
    "participants_names",
    "case_number_text",
    "decision_result",
]

MAX_ACT_TEXT_CHARS = 30_000
SNIPPET_CHARS = 400


class CourtPracticeSearcher:
    def __init__(self, client: OpenSearch, config: OpenSearchConfig):
        self._client = client
        self._config = config

    def _build_filters(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        result_type: str | None = None,
    ) -> list[dict[str, Any]]:
        filters: list[dict[str, Any]] = []
        if date_from or date_to:
            range_filter: dict[str, str] = {}
            if date_from:
                range_filter["gte"] = date_from
            if date_to:
                range_filter["lte"] = date_to
            filters.append({"range": {"decision_date": range_filter}})
        if result_type:
            filters.append({"term": {"result_type": result_type}})
        return filters

    def _build_query_body(
        self,
        query: str,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        result_type: str | None = None,
        size: int | None = None,
    ) -> dict[str, Any]:
        filters = self._build_filters(date_from=date_from, date_to=date_to, result_type=result_type)
        bool_query: dict[str, Any] = {
            "must": [
                {
                    "multi_match": {
                        "query": query,
                        "fields": SEARCH_FIELDS,
                        "type": "best_fields",
                        "operator": "or",
                    }
                }
            ]
        }
        if filters:
            bool_query["filter"] = filters

        return {
            "size": size or self._config.top_k * 3,
            "query": {"bool": bool_query},
            "highlight": {
                "fields": {
                    "act_text": {
                        "fragment_size": SNIPPET_CHARS,
                        "number_of_fragments": 2,
                        "no_match_size": SNIPPET_CHARS,
                    }
                },
                "pre_tags": ["**"],
                "post_tags": ["**"],
            },
        }

    def _hits_to_ranked(self, hits: list[dict]) -> list[RankedDocument]:
        ranked: list[RankedDocument] = []
        for hit in hits:
            source = hit.get("_source") or {}
            doc_id = source.get("uid") or hit.get("_id")
            if not doc_id:
                continue
            highlights = (hit.get("highlight") or {}).get("act_text") or []
            if not highlights and source.get("act_text"):
                highlights = [source["act_text"][:SNIPPET_CHARS]]
            ranked.append(RankedDocument(doc_id=doc_id, source=source, highlights=highlights))
        return ranked

    def search_sync(
        self,
        queries: list[str],
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        result_type: str | None = None,
    ) -> list[RankedDocument]:
        cleaned_queries = [q.strip() for q in queries if isinstance(q, str) and q.strip()]
        if not cleaned_queries:
            return []

        if len(cleaned_queries) == 1:
            body = self._build_query_body(
                cleaned_queries[0],
                date_from=date_from,
                date_to=date_to,
                result_type=result_type,
            )
            response = self._client.search(index=self._config.index_alias, body=body)
            return self._hits_to_ranked(response.get("hits", {}).get("hits", []))[: self._config.top_k]

        header = {"index": self._config.index_alias}
        msearch_body: list[dict] = []
        for query in cleaned_queries:
            msearch_body.append(header)
            msearch_body.append(
                self._build_query_body(
                    query,
                    date_from=date_from,
                    date_to=date_to,
                    result_type=result_type,
                )
            )

        response = self._client.msearch(body=msearch_body)
        responses = response.get("responses") or []
        result_lists = [
            self._hits_to_ranked(resp.get("hits", {}).get("hits", []))
            for resp in responses
            if not resp.get("error")
        ]
        return reciprocal_rank_fusion(result_lists, top_k=self._config.top_k)

    async def search(
        self,
        queries: list[str],
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        result_type: str | None = None,
    ) -> list[RankedDocument]:
        return await asyncio.to_thread(
            self.search_sync,
            queries,
            date_from=date_from,
            date_to=date_to,
            result_type=result_type,
        )

    def get_decision_sync(self, decision_id: str) -> dict | None:
        try:
            response = self._client.get(index=self._config.index_alias, id=decision_id)
        except Exception:
            logger.exception("Failed to fetch decision", extra={"decision_id": decision_id})
            return None
        return response.get("_source")

    async def get_decision(self, decision_id: str) -> dict | None:
        return await asyncio.to_thread(self.get_decision_sync, decision_id)


def format_search_results(results: list[RankedDocument]) -> str:
    if not results:
        return "По запросу судебная практика не найдена."

    blocks: list[str] = []
    for index, doc in enumerate(results, start=1):
        source = doc.source
        snippet = doc.highlights[0] if doc.highlights else (source.get("act_text") or "")[:SNIPPET_CHARS]
        blocks.append(
            "\n".join(
                [
                    f"{index}. uid: {source.get('uid', doc.doc_id)}",
                    f"   Дело: {source.get('case_number', '—')}",
                    f"   Суд: {source.get('court_name', '—')}",
                    f"   Дата решения: {source.get('decision_date', '—')}",
                    f"   Результат: {source.get('decision_result', '—')} ({source.get('result_type', '—')})",
                    f"   Категория: {source.get('category', '—')}",
                    f"   Фрагмент: {snippet}",
                ]
            )
        )
    return "\n\n".join(blocks)


def format_decision_document(doc: dict) -> str:
    act_text = doc.get("act_text") or ""
    truncated = False
    if len(act_text) > MAX_ACT_TEXT_CHARS:
        act_text = act_text[:MAX_ACT_TEXT_CHARS]
        truncated = True

    header = "\n".join(
        [
            f"uid: {doc.get('uid', '—')}",
            f"Дело: {doc.get('case_number', '—')}",
            f"Суд: {doc.get('court_name', '—')}",
            f"Судья: {doc.get('judge', '—')}",
            f"Дата решения: {doc.get('decision_date', '—')}",
            f"Результат: {doc.get('decision_result', '—')} ({doc.get('result_type', '—')})",
            f"Категория: {doc.get('category', '—')}",
            f"Стороны: {doc.get('participants_names', '—')}",
        ]
    )
    suffix = "\n\n[Текст обрезан — полный акт длиннее лимита контекста]" if truncated else ""
    return f"{header}\n\n---\n\n{act_text}{suffix}"
