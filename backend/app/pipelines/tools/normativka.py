"""Нормативка tools — statutes in their CURRENT redaction from the official corpus.

Replaces free web search for norms: the model formulates queries (it is the
semantic layer translating a lawyer's wording into statutory language), the
corpus is our own index of pravo.gov.ru texts, so the redaction is current by
construction.

Mirrors the court-practice tool wiring: search snippets are stored inline in
chat history; full article texts are heavy, so replay keeps only the article
id and rehydrates from OpenSearch.
"""
import logging
from typing import Annotated

from langchain_core.tools import tool

from app.normativka.acts import CODICES, resolve_act
from app.pipelines.tools.base import InlineResultHandler, ToolResultHandler, ToolSpec
from app.search import OpenSearchConfig, build_opensearch_client
from app.search.normativka import (
    NormativkaSearcher,
    format_statute_article,
    format_statute_results,
)

logger = logging.getLogger(__name__)

# The act vocabulary lives next to the parameter it documents (the system
# prompt keeps only the high-level rules). Built from the canonical codex
# table so it never drifts from what the index actually contains.
_KNOWN_ACTS_DOC = "Кодексы в корпусе (используй эти сокращения): " + "; ".join(
    f"{act.aliases[0] if act.aliases else act.name} — {act.name}" for act in CODICES
)

_ACTS_PARAM_DOC = (
    "Optional list of act references to restrict the search to, e.g. "
    '["ТК РФ"] or ["ГК РФ часть 2", "ЖК РФ"]. Omit (None) to search the whole '
    "corpus. " + _KNOWN_ACTS_DOC
)


class StatuteArticleHandler(ToolResultHandler):
    """Persist only the article reference; rehydrate the text from OpenSearch."""

    def __init__(self, searcher: NormativkaSearcher):
        self._searcher = searcher

    async def capture(self, *, args: dict, content: str) -> dict:
        return {"act": args.get("act", ""), "article": args.get("article", "")}

    async def run(self, *, args: dict, state: dict) -> str:
        act_ref = state.get("act") or args.get("act") or ""
        article_number = state.get("article") or args.get("article") or ""
        act = resolve_act(act_ref)
        if not act:
            return f"[Норма недоступна: акт «{act_ref}» не распознан]"
        source = await self._searcher.resolve(act.nd, article_number)
        if not source:
            return f"[Статья {article_number} ({act.name}) временно недоступна]"
        return format_statute_article(source)


def normativka_tool_specs(searcher: NormativkaSearcher) -> list[ToolSpec]:
    @tool
    async def search_normativka(
        queries: list[str],
        acts: Annotated[list[str] | None, _ACTS_PARAM_DOC] = None,
    ) -> str:
        """Search Russian statutes (кодексы и федеральные законы) in their
        CURRENT official redaction.

        Pass 1-4 complementary queries phrased in formal statutory language —
        translate the client's everyday wording into legal terminology first
        («могут ли уволить на больничном» → «расторжение трудового договора по
        инициативе работодателя в период временной нетрудоспособности»).
        Each query is a focused noun phrase; do not repeat the same words
        across queries. Use acts to restrict to specific codes when the branch
        of law is clear. Returns article snippets with ids; call
        get_statute_article for the full text of a specific norm.
        """
        if not queries:
            return "Не переданы поисковые запросы. Укажите от 1 до 4 запросов."
        cleaned = [q.strip() for q in queries if isinstance(q, str) and q.strip()][:4]
        if not cleaned:
            return "Все поисковые запросы пустые."

        act_nds: list[str] | None = None
        unknown: list[str] = []
        if acts:
            resolved = [(ref, resolve_act(ref)) for ref in acts if isinstance(ref, str) and ref.strip()]
            act_nds = [act.nd for _, act in resolved if act]
            unknown = [ref for ref, act in resolved if not act]
            if not act_nds:
                act_nds = None  # ни один фильтр не распознан — ищем по всему корпусу

        results = await searcher.search(cleaned, act_nds=act_nds)
        formatted = format_statute_results(results)
        if unknown:
            formatted += (
                "\n\n[Примечание: фильтры не распознаны и не применены: "
                + ", ".join(unknown)
                + ". Доступные акты перечислены в описании параметра acts.]"
            )
        return formatted

    @tool
    async def get_statute_article(act: str, article: str) -> str:
        """Fetch the full CURRENT text of a specific statute article by exact
        reference, e.g. act="ТК РФ", article="81" or act="НК РФ часть 2",
        article="333.19". Use for точные ссылки на нормы; для тематического
        поиска используй search_normativka."""
        if not act or not act.strip():
            return "Не указан акт (например, «ТК РФ»)."
        if not article or not article.strip():
            return "Не указан номер статьи (например, «81» или «333.19»)."

        known = resolve_act(act)
        if not known:
            return (
                f"Акт «{act}» не распознан. " + _KNOWN_ACTS_DOC
            )
        source = await searcher.resolve(known.nd, article)
        if not source:
            return (
                f"Статья {article.strip()} в акте «{known.name}» не найдена. "
                "Проверьте номер статьи (для дробных номеров используйте точки: 333.19)."
            )
        return format_statute_article(source)

    return [
        # Search snippets are small — stored inline and replayed verbatim.
        ToolSpec(search_normativka, InlineResultHandler()),
        # Full article text is heavy — store only the reference, rehydrate.
        ToolSpec(get_statute_article, StatuteArticleHandler(searcher)),
    ]


def try_build_tool_specs(app_config: dict) -> list[ToolSpec]:
    """Build нормативка tools from config, or [] when OpenSearch is absent."""
    cfg = (app_config or {}).get("opensearch") or {}
    if not cfg.get("url"):
        logger.warning("OpenSearch not configured; normativka tools disabled")
        return []
    os_config = OpenSearchConfig.model_validate(cfg)
    searcher = NormativkaSearcher(build_opensearch_client(os_config), os_config)
    logger.info(
        "Normativka search enabled",
        extra={"opensearch_url": os_config.url, "index": os_config.normativka_index_alias},
    )
    return normativka_tool_specs(searcher)
