"""Document drafting tool — drafts a full procedural document from the chat
context and attaches it as a downloadable .docx artifact.

The model calls ``draft_document(request)`` when the lawyer wants a document as a
file. The tool receives the WHOLE conversation (via ``InjectedState``), drafts
the complete text under a focused drafting system prompt (separate from the main
assistant prompt), then segments it into typed blocks and stores them in
``tool_state``. The .docx is rebuilt render-on-demand from those stored blocks
(deterministic, no LLM) when the chip is clicked — drafting runs ONCE per turn,
never per click.

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
from app.services.docx_drafting import segment_to_blocks

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


def _conversation_for_drafting(messages: list) -> list:
    """Clean message list for the drafting LLM: keep human/assistant text and
    tool results (as context), drop the main system prompt and tool-call
    metadata so the focused drafting prompt fully governs the call."""
    convo: list = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            content = _text(msg.content).strip()
            if content:
                convo.append(HumanMessage(content=content))
        elif isinstance(msg, AIMessage):
            content = _text(msg.content).strip()
            if content:
                convo.append(AIMessage(content=content))
        elif isinstance(msg, ToolMessage):
            content = _text(msg.content).strip()
            if content:
                convo.append(HumanMessage(content=f"[Материалы по делу]\n{content}"))
    return convo


class DraftHandler(ToolResultHandler):
    """Persist the drafted document (status/file_name/blocks) for render-on-demand.

    ``capture`` stores the full draft; ``run`` replays only a compact note into
    future-turn context so the whole document doesn't bloat history (capture-rich
    / replay-cheap, like CourtDecisionHandler)."""

    async def capture(self, *, args: dict, content: str) -> dict:
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return {"status": "failed", "file_name": "Документ", "blocks": []}
        return {
            "status": data.get("status", "failed"),
            "file_name": data.get("file_name") or "Документ",
            "blocks": data.get("blocks") or [],
        }

    async def run(self, *, args: dict, state: dict) -> str:
        file_name = state.get("file_name") or "документ"
        if state.get("status") != "ready":
            return f"[Не удалось оформить документ «{file_name}».]"
        return f"[Документ «{file_name}» подготовлен и доступен пользователю для скачивания.]"


def drafting_tool_specs(drafting_llm: ChatOpenAI, segmenter: ChatOpenAI) -> list[ToolSpec]:
    """Build the terminal drafting tool, closing over the drafting + segmenter LLMs."""

    @tool
    async def draft_document(request: str, state: Annotated[dict, InjectedState]) -> str:
        """Составить полный процессуальный документ и приложить его файлом .docx.

        Вызывай, когда юрист хочет получить процессуальный документ (иск,
        возражения, ходатайство, пояснения, жалобу) ФАЙЛОМ для подачи в суд.
        Инструмент сам напишет полный текст документа, опираясь на весь контекст
        переписки (запрос юриста, приложенные материалы, найденную практику, твой
        анализ), и приложит готовый .docx.

        request — кратко: какой документ нужен и его ключевые требования
        (например «Возражения на заявление о пропуске срока исковой давности»).
        Полный текст документа в свой ответ НЕ пиши — его готовит инструмент.
        """
        convo = _conversation_for_drafting(state.get("messages") or [])
        draft_input = (
            [SystemMessage(content=get_drafting_prompt())]
            + convo
            + [HumanMessage(content=f"Составь полный текст процессуального документа. Что нужно: {request}")]
        )
        try:
            response = await drafting_llm.ainvoke(draft_input)
            doc_text = _text(response.content).strip()
        except Exception:  # noqa: BLE001 - any drafting failure -> failed artifact
            logger.exception("draft_document drafting failed")
            return _failure()
        if not doc_text:
            return _failure()

        try:
            drafted = await segment_to_blocks(segmenter, doc_text)
        except Exception:  # noqa: BLE001 - any segmentation failure -> failed artifact
            logger.exception("draft_document segmentation failed")
            return _failure()

        return json.dumps(
            {
                "status": "ready",
                "file_name": drafted.file_name,
                "blocks": [block.model_dump() for block in drafted.blocks],
            },
            ensure_ascii=False,
        )

    return [ToolSpec(draft_document, DraftHandler(), terminal=True)]
