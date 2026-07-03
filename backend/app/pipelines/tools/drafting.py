"""Document drafting tool — drafts a full procedural document from the chat
context and attaches it as a downloadable .docx artifact.

The model calls ``draft_document()`` (no args) when the lawyer wants a document
as a file. The tool receives the WHOLE conversation (via ``InjectedState``),
drafts the complete text under a focused drafting system prompt (separate from
the main assistant prompt), then segments it into typed blocks and stores them
in ``tool_state``. The .docx is rebuilt render-on-demand from those stored
blocks (deterministic, no LLM) when the chip is clicked — drafting runs ONCE
per turn, never per click.

Being **terminal** (``ToolSpec.terminal=True``) ends the turn after drafting: the
document is the deliverable, and the visible bubble is the short note the model
writes alongside the call (the document itself is NOT echoed into the chat).
"""
import json
import logging
from typing import Annotated

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import InjectedState

from app.pipelines.tools.base import ToolResultHandler, ToolSpec
from app.rag_core.prompt import get_drafting_prompt
from app.services.docx_drafting import assemble_blocks, classify_lines, tokenize_draft

logger = logging.getLogger(__name__)

DRAFT_TOOL_NAME = "draft_document"


def _text(content) -> str:
    """Flatten message content (str or list of content parts) to text. Local copy
    to avoid importing app.pipelines.messages (would create an import cycle:
    messages -> tools.base -> tools/__init__ -> drafting)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return str(content or "")


def _failure() -> str:
    return json.dumps({"status": "failed", "file_name": "Документ", "blocks": []}, ensure_ascii=False)


def _serialize_history(messages: list) -> str:
    """Serialize the chat history to JSON for the drafting system prompt.

    Keeps human/assistant text and tool results (court practice, etc.) as the
    case context; drops the main assistant SystemMessage and empty/tool-call-only
    messages. The drafting LLM reads this as the single source of what to draft.
    The LAST lawyer message is marked as the actual request — without the marker
    the drafter has to guess which of several discussed documents is wanted.
    """
    items: list[dict] = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            content = _text(msg.content).strip()
            if content:
                items.append({"role": "Юрист", "content": content})
        elif isinstance(msg, AIMessage):
            content = _text(msg.content).strip()
            if content:
                items.append({"role": "Ассистент", "content": content})
        elif isinstance(msg, ToolMessage):
            content = _text(msg.content).strip()
            if content:
                items.append({"role": "Материалы (результат инструмента)", "content": content})
        # SystemMessage (the main assistant prompt) is intentionally skipped.
    for item in reversed(items):
        if item["role"] == "Юрист":
            item["role"] = (
                "Юрист — АКТУАЛЬНЫЙ ЗАПРОС (составляй документ по нему, "
                "остальная переписка — контекст)"
            )
            break
    return json.dumps(items, ensure_ascii=False, indent=2)


def _blocks_text(blocks: list) -> str:
    """Плоский текст документа из сохранённых блоков — реплеится в историю,
    чтобы следующий драфт правил предыдущую версию, а не писал вслепую заново."""
    lines: list[str] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "table":
            for row in b.get("rows") or []:
                cells = row.get("cells") if isinstance(row, dict) else None
                if cells:
                    lines.append(" | ".join(str(c) for c in cells))
        else:
            lines.append(str(b.get("text") or ""))
    return "\n".join(lines).strip()


class DraftHandler(ToolResultHandler):
    """Persist the drafted document (status/file_name/blocks) for render-on-demand.

    ``capture`` stores the full draft; ``run`` replays the note PLUS the flat
    document text reconstructed from the stored blocks. Replaying the text is
    what makes iterative edits possible: without it «поправь пункт 3» is a
    blind full redraft that silently loses the agreed wording of the previous
    version (the ~few-thousand-token history cost is the accepted price)."""

    async def capture(self, *, args: dict, content: str) -> dict:
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return {"status": "failed", "file_name": "Документ", "blocks": []}
        return {
            "status": data.get("status", "failed"),
            "file_name": data.get("file_name") or "Документ",
            "blocks": data.get("blocks") or [],
            "degraded": bool(data.get("degraded")),
        }

    async def run(self, *, args: dict, state: dict) -> str:
        file_name = state.get("file_name") or "документ"
        if state.get("status") != "ready":
            return f"[Не удалось оформить документ «{file_name}».]"
        note = f"[Документ «{file_name}» подготовлен и доступен пользователю для скачивания.]"
        text = _blocks_text(state.get("blocks") or [])
        if not text:
            return note
        return (
            note
            + "\nТекущий текст документа (при просьбе юриста что-то изменить — "
            + "составляй новую версию на его основе):\n"
            + text
        )


def drafting_tool_specs(drafting_llm: ChatOpenAI, segmenter: ChatOpenAI) -> list[ToolSpec]:
    """Build the terminal drafting tool, closing over the drafting + segmenter LLMs."""

    @tool
    async def draft_document(state: Annotated[dict, InjectedState]) -> str:
        """Составить полный процессуальный документ и приложить его файлом .docx.

        Вызывай этот инструмент БЕЗ ПАРАМЕТРОВ, когда юрист хочет получить
        процессуальный документ (иск, возражения, ходатайство, пояснения, жалобу)
        ФАЙЛОМ для подачи в суд — а также когда просит ПОПРАВИТЬ уже выданный
        документ: инструмент видит текст предыдущей версии и внесёт правки.
        Инструмент сам прочитает всю переписку и напишет полный текст документа,
        опираясь на весь контекст (запрос юриста, приложенные материалы,
        найденную практику, твой анализ), и приложит .docx.
        Полный текст документа в свой ответ НЕ пиши — его готовит инструмент.
        """
        history_json = _serialize_history(state.get("messages") or [])
        system = (
            get_drafting_prompt()
            + "\n\n# КОНТЕКСТ: полная история переписки с юристом\n"
            + "Ниже — вся переписка юриста с ассистентом в формате JSON. Именно из "
            + "неё вытекает, какой документ нужен, и все факты, реквизиты и "
            + "материалы дела. Опирайся ТОЛЬКО на этот контекст:\n\n"
            + history_json
        )
        draft_input = [
            SystemMessage(content=system),
            HumanMessage(content="Составь полный текст процессуального документа по контексту выше."),
        ]
        try:
            response = await drafting_llm.ainvoke(draft_input)
            doc_text = _text(response.content).strip()
        except Exception:  # noqa: BLE001 - any drafting failure -> failed artifact
            logger.exception("draft_document drafting failed")
            return _failure()
        finish_reason = (getattr(response, "response_metadata", None) or {}).get(
            "finish_reason"
        )
        # Подстрочное сравнение, не точное: при streaming=True OpenRouter шлёт
        # finish_reason в двух чанках, а langchain склеивает строки конкатенацией
        # -> в метаданных реально лежит "lengthlength" (и "stopstop" при норме).
        if finish_reason and "length" in finish_reason:
            # Обрыв по max_tokens: непустой, но неполный текст. Обрезанный
            # документ не должен уходить юристу со статусом ready.
            logger.error(
                "draft_document truncated at max_tokens; failing the artifact",
                extra={"draft_chars": len(doc_text)},
            )
            return _failure()
        if not doc_text:
            return _failure()

        # Deterministic tokenizer -> cheap line classifier -> reassemble. The
        # classifier returns TYPES only (no document text), so its output stays
        # tiny. Spacers and tables are detected in code, never re-emitted.
        units = tokenize_draft(doc_text)
        content_units = [u for u in units if u["kind"] == "content"]
        if not content_units:
            return _failure()
        classified = None
        for attempt in (1, 2):
            try:
                classified = await classify_lines(segmenter, content_units)
                break
            except Exception:  # noqa: BLE001 - retry once, then degrade
                logger.exception(
                    "draft_document classification failed (attempt %s/2)", attempt
                )
        degraded = classified is None
        if degraded:
            # Документ уйдёт сплошным body без шапки/заголовка/подписи — флаг
            # degraded делает эту деградацию видимой в tool_state и логах.
            logger.warning("draft_document degraded: rendering as plain body")
            types_by_id = {}
            file_name = "Документ"
        else:
            types_by_id = {line.id: line.type for line in classified.lines}
            file_name = classified.file_name or "Документ"
            missing = [u["id"] for u in content_units if u["id"] not in types_by_id]
            if missing:
                logger.warning(
                    "draft_document classification missed %d/%d lines; they fall back to body",
                    len(missing),
                    len(content_units),
                )

        blocks = assemble_blocks(units, types_by_id)
        return json.dumps(
            {"status": "ready", "file_name": file_name, "blocks": blocks, "degraded": degraded},
            ensure_ascii=False,
        )

    return [ToolSpec(draft_document, DraftHandler(), terminal=True)]
