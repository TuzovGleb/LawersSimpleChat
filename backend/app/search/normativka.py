"""Statute (нормативка) search queries and result formatting.

Two access paths, deliberately separate:

* ``search`` — topical BM25 over article documents, multi-query + RRF. The
  chat model is the semantic layer: it translates the lawyer's wording into
  the statute's formal language before calling us (см. память проекта:
  векторный ретривал сознательно не используется).
* ``resolve`` — exact norm lookup by (act nd, article number) with term
  filters and no scoring at all: «ст. 81 ТК РФ» must never depend on ranking.
"""
import asyncio
import logging
from typing import Any

from opensearchpy import OpenSearch

from app.search.client import OpenSearchConfig
from app.search.normativka_index import normalize_act_number, normalize_article_number
from app.search.rrf import RankedDocument, reciprocal_rank_fusion

logger = logging.getLogger(__name__)

# Field boosts exploit the statute corpus's structure: an article's own title
# is the densest signal there is; the act name catches «трудовой кодекс …»
# phrasings; chapter headers give topical context; body text is the base.
NORMATIVKA_SEARCH_FIELDS = [
    "article_title^4",
    "act_name^2.5",
    "act_aliases^2.5",
    # «44-ФЗ», «152-ФЗ» — как юрист называет закон в запросе.
    "act_number.text^2.5",
    "chapter_path^1.5",
    "article_text",
]

# Lex-specialis tie-break: ФЗ детализируют кодексы, so on equal textual
# relevance the more specific layer surfaces first. Kept deliberately small —
# term matching must dominate (профильные ФЗ с «трудовой» лексикой не должны
# выталкивать ст. 81 ТК). Tuned by the golden set, not by intuition.
FZ_SPECIFICITY_BOOST = 0.4

MAX_ARTICLE_TEXT_CHARS = 30_000
SNIPPET_CHARS = 400


class NormativkaSearcher:
    def __init__(self, client: OpenSearch, config: OpenSearchConfig):
        self._client = client
        self._config = config

    @property
    def _index(self) -> str:
        return self._config.normativka_index_alias

    @property
    def _top_k(self) -> int:
        return self._config.normativka_top_k

    def _build_query_body(self, query: str, *, act_nds: list[str] | None = None) -> dict[str, Any]:
        bool_query: dict[str, Any] = {
            "must": [
                {
                    "multi_match": {
                        "query": query,
                        "fields": NORMATIVKA_SEARCH_FIELDS,
                        "type": "best_fields",
                        "operator": "or",
                    }
                }
            ],
            # Score-only boosts (no minimum_should_match => never filter):
            # exact legal phrasing first, then the lex-specialis tie-break.
            "should": [
                {"match_phrase": {"article_text": {"query": query, "slop": 2, "boost": 2.0}}},
                {"term": {"act_kind": {"value": "fz", "boost": FZ_SPECIFICITY_BOOST}}},
            ],
        }
        if act_nds:
            bool_query["filter"] = [{"terms": {"act_nd": act_nds}}]
        return {
            "size": self._top_k * 3,
            "query": {"bool": bool_query},
            "highlight": {
                "fields": {
                    "article_text": {
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
            doc_id = source.get("article_id") or hit.get("_id")
            if not doc_id:
                continue
            highlights = (hit.get("highlight") or {}).get("article_text") or []
            if not highlights and source.get("article_text"):
                highlights = [source["article_text"][:SNIPPET_CHARS]]
            ranked.append(RankedDocument(doc_id=doc_id, source=source, highlights=highlights))
        return ranked

    def search_sync(self, queries: list[str], *, act_nds: list[str] | None = None) -> list[RankedDocument]:
        cleaned = [q.strip() for q in queries if isinstance(q, str) and q.strip()]
        if not cleaned:
            return []

        if len(cleaned) == 1:
            body = self._build_query_body(cleaned[0], act_nds=act_nds)
            response = self._client.search(index=self._index, body=body)
            return self._hits_to_ranked(response.get("hits", {}).get("hits", []))[: self._top_k]

        msearch_body: list[dict] = []
        for query in cleaned:
            msearch_body.append({"index": self._index})
            msearch_body.append(self._build_query_body(query, act_nds=act_nds))
        response = self._client.msearch(body=msearch_body)
        result_lists = []
        for query, resp in zip(cleaned, response.get("responses") or []):
            if resp.get("error"):
                logger.warning(
                    "normativka msearch sub-query failed",
                    extra={"index": self._index, "query": query, "os_error": resp["error"]},
                )
                continue
            result_lists.append(self._hits_to_ranked(resp.get("hits", {}).get("hits", [])))
        return reciprocal_rank_fusion(result_lists, top_k=self._top_k)

    async def search(self, queries: list[str], *, act_nds: list[str] | None = None) -> list[RankedDocument]:
        return await asyncio.to_thread(self.search_sync, queries, act_nds=act_nds)

    def resolve_act_sync(self, act_ref: str) -> list[dict]:
        """Find acts in the corpus by a lawyer's reference to them.

        Handles both ways an act gets named: by number («44-ФЗ», «2300-I») and
        by name («О защите прав потребителей», «Трудовой кодекс»). Resolution
        goes through the INDEX rather than a hardcoded table, because the
        corpus holds ~1000 federal laws that no table could carry.

        CAVEAT verified live: federal-law numbers are NOT unique — they restart
        every year, so «14-ФЗ» is both «Об обществах с ограниченной
        ответственностью» (1998) and «Об упразднении некоторых районных судов
        Самарской области» (2013). A bare number therefore cannot be resolved
        to one act; the caller must disambiguate. Candidates carry ``articles``
        (how many articles the act has in the index) and ``act_date``, which is
        what tells the famous law from its same-numbered namesakes.
        """
        ref = (act_ref or "").strip()
        if not ref:
            return []

        number = normalize_act_number(ref)
        should: list[dict[str, Any]] = [
            # Название целиком/почти целиком — сильнейший сигнал.
            {"match_phrase": {"act_name": {"query": ref, "boost": 6.0}}},
            {"match": {"act_name": {"query": ref, "operator": "and", "boost": 3.0}}},
            {"match_phrase": {"act_aliases": {"query": ref, "boost": 6.0}}},
            # Юрист называет закон не его официальным именем: «закон о
            # банкротстве» вместо «О несостоятельности (банкротстве)». Требовать
            # ВСЕ слова нельзя — лишнее «закон» обнуляет совпадение; поэтому
            # мягкий вариант с большинством слов и низким бустом, чтобы точное
            # название всегда оставалось выше.
            {
                "match": {
                    "act_name": {"query": ref, "operator": "or", "minimum_should_match": "60%", "boost": 1.0}
                }
            },
        ]
        if number:
            should.append({"term": {"act_number": {"value": number, "boost": 10.0}}})

        body = {
            "size": 0,
            "query": {"bool": {"should": should, "minimum_should_match": 1}},
            "aggs": {
                "acts": {
                    # По релевантности названия; при равенстве (типичный случай
                    # для чистого номера) впереди акт с большим числом статей —
                    # у «громких» законов их заметно больше, чем у точечных
                    # однодневок с тем же номером.
                    "terms": {
                        "field": "act_nd",
                        "size": 8,
                        "order": [{"score": "desc"}, {"_count": "desc"}],
                    },
                    "aggs": {
                        "score": {"max": {"script": "_score"}},
                        "meta": {
                            "top_hits": {
                                "size": 1,
                                "_source": [
                                    "act_nd",
                                    "act_kind",
                                    "act_name",
                                    "act_number",
                                    "act_date",
                                ],
                            }
                        },
                    },
                }
            },
        }
        response = self._client.search(index=self._index, body=body)
        acts: list[dict] = []
        for bucket in response.get("aggregations", {}).get("acts", {}).get("buckets", []):
            hits = bucket.get("meta", {}).get("hits", {}).get("hits", [])
            if not hits:
                continue
            act = dict(hits[0].get("_source") or {})
            act["articles"] = bucket.get("doc_count", 0)
            acts.append(act)
        return acts

    async def resolve_act(self, act_ref: str) -> list[dict]:
        return await asyncio.to_thread(self.resolve_act_sync, act_ref)

    def resolve_sync(self, act_nd: str, article_number: str) -> dict | None:
        """Exact article fetch: term filters, no scoring, no ranking."""
        body = {
            "size": 1,
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"act_nd": act_nd}},
                        {"term": {"article_number": normalize_article_number(article_number)}},
                    ]
                }
            },
        }
        response = self._client.search(index=self._index, body=body)
        hits = response.get("hits", {}).get("hits", [])
        return (hits[0].get("_source") or None) if hits else None

    async def resolve(self, act_nd: str, article_number: str) -> dict | None:
        return await asyncio.to_thread(self.resolve_sync, act_nd, article_number)


def _article_ref(source: dict) -> str:
    """«ст. 81 — Трудовой кодекс Российской Федерации (197-ФЗ)»."""
    act = source.get("act_name") or "—"
    number = source.get("act_number")
    suffix = f" ({number})" if number else ""
    return f"ст. {source.get('article_number', '—')} — {act}{suffix}"


def format_statute_results(results: list[RankedDocument]) -> str:
    if not results:
        return "По запросу нормы не найдены."

    blocks: list[str] = []
    for index, doc in enumerate(results, start=1):
        source = doc.source
        snippet = doc.highlights[0] if doc.highlights else (source.get("article_text") or "")[:SNIPPET_CHARS]
        # No internal ids in the output: the full-text tool is addressed by
        # (акт, номер статьи) from the «Норма» line, nothing consumes an id.
        lines = [
            f"{index}. Норма: {_article_ref(source)}",
            f"   Заголовок: {source.get('article_title', '—')}",
        ]
        if source.get("chapter_path"):
            lines.append(f"   Расположение: {source['chapter_path']}")
        lines.append(f"   Фрагмент: {snippet}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def format_statute_article(source: dict) -> str:
    text = source.get("article_text") or ""
    truncated = False
    if len(text) > MAX_ARTICLE_TEXT_CHARS:
        text = text[:MAX_ARTICLE_TEXT_CHARS]
        truncated = True

    lines = [
        f"Норма: {_article_ref(source)}",
        f"Заголовок: {source.get('article_title', '—')}",
    ]
    if source.get("chapter_path"):
        lines.append(f"Расположение: {source['chapter_path']}")
    if source.get("source_url"):
        lines.append(f"Официальный текст: {source['source_url']}")
    lines.append("Текст приведён в действующей редакции (официальный портал pravo.gov.ru).")
    suffix = "\n\n[Текст обрезан — статья длиннее лимита контекста]" if truncated else ""
    return "\n".join(lines) + f"\n\n---\n\n{text}{suffix}"
