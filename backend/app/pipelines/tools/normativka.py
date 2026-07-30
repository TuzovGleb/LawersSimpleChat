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
from app.search.normativka_index import normalize_act_number

logger = logging.getLogger(__name__)

# The act vocabulary lives next to the parameter it documents (the system
# prompt keeps only the high-level rules). Built from the canonical codex
# table so it never drifts from what the index actually contains.
_KNOWN_ACTS_DOC = "Кодексы в корпусе (используй эти сокращения): " + "; ".join(
    f"{act.aliases[0] if act.aliases else act.name} — {act.name}" for act in CODICES
)

_ACTS_PARAM_DOC = (
    "Optional list of act references to restrict the search to, e.g. "
    '["ТК РФ"], ["ГК РФ часть 2", "ЖК РФ"] or ["44-ФЗ"]. Federal laws can be '
    "named by number («44-ФЗ», «152-ФЗ») or by title («О защите прав "
    "потребителей»). Omit (None) to search the whole corpus. " + _KNOWN_ACTS_DOC
)


def _is_bare_number(act_ref: str) -> bool:
    """True for «44-ФЗ», «№ 152-ФЗ», «127» — a reference with no words in it."""
    return bool(normalize_act_number(act_ref))


def _format_candidates(candidates: list[dict]) -> str:
    lines = []
    for act in candidates:
        year = (act.get("act_date") or "")[:4]
        lines.append(
            f"— {act.get('act_number') or '—'}"
            + (f" от {year}" if year else "")
            + f": {act.get('act_name', '')[:90]} (статей: {act.get('articles', 0)})"
        )
    return "\n".join(lines)


async def _resolve_act_candidates(searcher: NormativkaSearcher, act_ref: str) -> list[dict]:
    """Candidate acts for a lawyer's reference, best match first.

    Codex aliases («ТК РФ», «ГПК») come from the curated table — they are
    ambiguous by nature (a bare «ТК» must mean the labour code, not the 1993
    customs one) and cheap to answer without a query. Everything else —
    ~1000 federal laws — resolves against the index, since no table can hold
    them.
    """
    known = resolve_act(act_ref)
    if known:
        return [{"act_nd": known.nd, "act_name": known.name, "act_number": known.number}]
    return await searcher.resolve_act(act_ref)


class StatuteArticleHandler(ToolResultHandler):
    """Persist only the article reference; rehydrate the text from OpenSearch."""

    def __init__(self, searcher: NormativkaSearcher):
        self._searcher = searcher

    async def capture(self, *, args: dict, content: str) -> dict:
        return {"act": args.get("act", ""), "article": args.get("article", "")}

    async def run(self, *, args: dict, state: dict) -> str:
        act_ref = state.get("act") or args.get("act") or ""
        article_number = state.get("article") or args.get("article") or ""
        candidates = await _resolve_act_candidates(self._searcher, act_ref)
        if not candidates:
            return f"[Норма недоступна: акт «{act_ref}» не распознан]"
        top = candidates[0]
        source = await self._searcher.resolve(top.get("act_nd", ""), article_number)
        if not source:
            return f"[Статья {article_number} ({top.get('act_name', '')}) временно недоступна]"
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
        of law is clear. Returns article snippets; for the full text of a
        specific norm call get_statute_article with the act and article number
        shown in that snippet's «Норма» line.
        """
        if not queries:
            return "Не переданы поисковые запросы. Укажите от 1 до 4 запросов."
        cleaned = [q.strip() for q in queries if isinstance(q, str) and q.strip()][:4]
        if not cleaned:
            return "Все поисковые запросы пустые."

        act_nds: list[str] | None = None
        unknown: list[str] = []
        if acts:
            act_nds = []
            for ref in acts:
                if not isinstance(ref, str) or not ref.strip():
                    continue
                candidates = await _resolve_act_candidates(searcher, ref)
                if not candidates:
                    unknown.append(ref)
                    continue
                # Номера ФЗ повторяются по годам, поэтому по чистому номеру
                # фильтруем по ВСЕМ одноимённым актам — это строго лучше, чем
                # угадать один и молча потерять нужный.
                limit = None if _is_bare_number(ref) else 1
                act_nds.extend(c.get("act_nd", "") for c in candidates[:limit] if c.get("act_nd"))
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
        reference. The act can be named either way lawyers name it: by short
        title (act="ТК РФ", act="Закон о защите прав потребителей") or by
        number (act="44-ФЗ", act="152-ФЗ"). Articles with dotted numbers keep
        the dots: article="333.19", article="15.34.1". Use for точные ссылки на
        нормы; для тематического поиска используй search_normativka."""
        if not act or not act.strip():
            return "Не указан акт (например, «ТК РФ» или «44-ФЗ»)."
        if not article or not article.strip():
            return "Не указан номер статьи (например, «81» или «333.19»)."

        candidates = await _resolve_act_candidates(searcher, act)
        if not candidates:
            return (
                f"Акт «{act}» в корпусе не найден. Попробуй указать его номер («44-ФЗ») "
                f"или точное название. {_KNOWN_ACTS_DOC}"
            )

        # Номера федеральных законов повторяются каждый год: «14-ФЗ» — это и
        # «Об обществах с ограниченной ответственностью» (1998), и «Об
        # упразднении некоторых районных судов» (2013). По чистому номеру
        # выбирать за юриста нельзя — показываем варианты.
        if _is_bare_number(act) and len(candidates) > 1:
            return (
                f"Номер «{act}» носят несколько актов (номера ФЗ повторяются по годам). "
                f"Уточни, какой нужен — вызови инструмент с названием акта:\n"
                + _format_candidates(candidates[:5])
            )

        top = candidates[0]
        source = await searcher.resolve(top.get("act_nd", ""), article)
        if not source:
            hint = ""
            if len(candidates) > 1:
                hint = "\nМожет быть, имелся в виду другой акт:\n" + _format_candidates(candidates[1:4])
            return (
                f"Статья {article.strip()} в акте «{top.get('act_name', '')}» не найдена. "
                f"Проверьте номер статьи (для дробных номеров используйте точки: 333.19).{hint}"
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
