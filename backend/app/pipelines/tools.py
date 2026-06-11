"""LangChain tools for court practice search."""
from typing import Literal

from langchain_core.tools import BaseTool, tool

from app.search.search import CourtPracticeSearcher, format_decision_document, format_search_results


def build_court_practice_tools(searcher: CourtPracticeSearcher) -> list[BaseTool]:
    """Create court practice tools bound to a configured searcher."""

    @tool
    async def search_court_practice(
        queries: list[str],
        date_from: str | None = None,
        date_to: str | None = None,
        result_type: Literal["granted", "denied", "partial", "other"] | None = None,
    ) -> str:
        """Search court decisions by full text.

        Pass 1-4 complementary queries from different angles:
        legal norm, factual pattern, and key phrases from the dispute.
        Use date_from/date_to as YYYY-MM-DD. result_type filters outcome.
        Returns compact snippets; call get_court_decision for full act text.
        """
        if not queries:
            return "Не переданы поисковые запросы. Укажите от 1 до 4 запросов."

        cleaned = [query.strip() for query in queries if isinstance(query, str) and query.strip()][:4]
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

    return [search_court_practice, get_court_decision]
