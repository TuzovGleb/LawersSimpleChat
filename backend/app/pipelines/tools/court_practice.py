"""Court practice tools — the only module that knows about OpenSearch.

Plugs into the generic tool framework by returning ``ToolSpec`` objects. The
heavy full act text is never stored in chat history: ``get_court_decision``
keeps only the decision id and re-fetches the text from OpenSearch when the
turn is replayed into context.

``try_build_tool_specs`` owns the production wiring (reading config, creating
the OpenSearch client) so the app's composition root never sees the backend.
"""
import logging
from typing import Literal

from langchain_core.tools import tool

from app.pipelines.tools.base import InlineResultHandler, ToolResultHandler, ToolSpec
from app.search import CourtPracticeSearcher, OpenSearchConfig, build_opensearch_client
from app.search.search import format_decision_document, format_search_results

logger = logging.getLogger(__name__)


class CourtDecisionHandler(ToolResultHandler):
    """Persist only the decision id; rehydrate full text from OpenSearch."""

    def __init__(self, searcher: CourtPracticeSearcher):
        self._searcher = searcher

    async def capture(self, *, args: dict, content: str) -> dict:
        return {"decision_id": args.get("decision_id", "")}

    async def run(self, *, args: dict, state: dict) -> str:
        decision_id = state.get("decision_id") or args.get("decision_id") or ""
        if not decision_id:
            return "[Решение недоступно: отсутствует идентификатор]"
        document = await self._searcher.get_decision(decision_id)
        if not document:
            return f"[Решение {decision_id} временно недоступно]"
        return format_decision_document(document)


def court_practice_tool_specs(searcher: CourtPracticeSearcher) -> list[ToolSpec]:
    @tool
    async def search_court_practice(
        queries: list[str],
        date_from: str | None = None,
        date_to: str | None = None,
        result_type: Literal["granted", "denied", "partial", "other"] | None = None,
    ) -> str:
        """Search court decisions (суды общей юрисдикции) by full text.

        Pass 1-4 complementary queries from different angles (legal norm,
        dispute type, procedural angle). Phrase queries in the formal language
        of court decisions, NOT colloquial wording — translate the client's
        everyday description into legal terminology first. Each query is a
        focused 4-9 word noun phrase; do not repeat the same words across
        queries. Strip names, case numbers, amounts, addresses, dates — the
        corpus is anonymized and these only add noise.
        Use date_from/date_to as YYYY-MM-DD. result_type filters outcome.
        Returns compact snippets; call get_court_decision for full act text.
        """
        if not queries:
            return "Не переданы поисковые запросы. Укажите от 1 до 4 запросов."

        cleaned = [q.strip() for q in queries if isinstance(q, str) and q.strip()][:4]
        if not cleaned:
            return "Все поисковые запросы пустые."

        results = await searcher.search(
            cleaned,
            date_from=date_from,
            date_to=date_to,
            result_type=result_type,
        )
        return format_search_results(results)

    @tool
    async def get_court_decision(decision_id: str) -> str:
        """Fetch the full text of a court decision by id returned from search_court_practice."""
        if not decision_id or not decision_id.strip():
            return "Не указан идентификатор решения (id)."

        document = await searcher.get_decision(decision_id.strip())
        if not document:
            return f"Решение с id={decision_id} не найдено."
        return format_decision_document(document)

    return [
        # Search snippets are small — stored inline and replayed verbatim.
        ToolSpec(search_court_practice, InlineResultHandler()),
        # Full act text is heavy — store only the id, rehydrate from OpenSearch.
        ToolSpec(get_court_decision, CourtDecisionHandler(searcher)),
    ]


def try_build_tool_specs(app_config: dict) -> list[ToolSpec]:
    """Build court-practice tools from config, or [] when OpenSearch is absent.

    This is the production builder consumed by the tool registry: it reads its
    own slice of config and constructs the OpenSearch client. The app never
    learns what backend these tools use.
    """
    cfg = (app_config or {}).get("opensearch") or {}
    if not cfg.get("url"):
        logger.warning("OpenSearch not configured; court practice tools disabled")
        return []
    os_config = OpenSearchConfig.model_validate(cfg)
    searcher = CourtPracticeSearcher(build_opensearch_client(os_config), os_config)
    logger.info("Court practice search enabled", extra={"opensearch_url": os_config.url})
    return court_practice_tool_specs(searcher)
