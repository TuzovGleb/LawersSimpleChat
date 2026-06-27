"""Document drafting tool — turns a finished procedural text into a downloadable
.docx artifact attached to the turn.

This is a **terminal** tool (``ToolSpec.terminal=True``): when the model calls it,
the agent loop ends — the document is the turn's deliverable, not something to
summarize afterwards. The visible assistant message is the short note the model
writes alongside the call; the document itself is NOT echoed into the chat.

Persistence (render-on-demand): the tool segments the text into typed blocks
(stage 1) and ``DraftHandler.capture`` stores ``{status, file_name, blocks}`` in
``tool_state``. The .docx is rendered lazily from those blocks when the user
clicks the chip (see GET /chats/{id}/documents/{draft_id}). Nothing binary is
stored.
"""
import json
import logging

from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from app.pipelines.tools.base import ToolResultHandler, ToolSpec
from app.services.docx_drafting import segment_to_blocks

logger = logging.getLogger(__name__)

DRAFT_TOOL_NAME = "draft_document"


def _failure() -> str:
    return json.dumps({"status": "failed", "file_name": "Документ", "blocks": []}, ensure_ascii=False)


class DraftHandler(ToolResultHandler):
    """Persist the structured draft (status/file_name/blocks) for render-on-demand.

    ``capture`` stores the full draft state; ``run`` replays only a compact note
    into future-turn context so the whole document doesn't bloat the model's
    history (the same capture-rich / replay-cheap pattern as CourtDecisionHandler).
    """

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
        return f"[Документ «{file_name}» оформлен и доступен пользователю для скачивания.]"


def drafting_tool_specs(segmenter: ChatOpenAI) -> list[ToolSpec]:
    """Build the terminal drafting tool, closing over the segmentation LLM."""

    @tool
    async def draft_document(text: str) -> str:
        """Оформить готовый процессуальный документ в скачиваемый файл .docx.

        Вызывай этот инструмент, когда пользователь хочет получить процессуальный
        документ (иск, возражения, ходатайство, пояснения, жалобу) ФАЙЛОМ для
        подачи в суд. Передавай ПОЛНЫЙ текст документа в параметр ``text``.

        Сам текст документа НЕ дублируй в свой ответ пользователю — он попадёт
        в файл. В ответе напиши лишь короткую заметку (1–2 предложения), что
        документ подготовлен и доступен для скачивания.
        """
        if not text or not text.strip():
            return _failure()
        try:
            drafted = await segment_to_blocks(segmenter, text)
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
