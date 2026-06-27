"""Document drafting tool — a terminal MARKER that attaches a downloadable .docx
chip to the assistant's answer.

For now this tool is a stub: it does NOT format anything. Its only job is to
signal "this answer is a procedural document the lawyer may want as a file", so
the frontend shows a download chip. The model writes the document in its answer
text as usual; the .docx is built render-on-demand when the chip is clicked —
the render endpoint reads that message's text, segments it and renders it
(see GET /chats/{id}/documents/{draft_id}). Later this tool can become the real
drafter (producing the structured document itself).

Being **terminal** (``ToolSpec.terminal=True``) keeps the answer text as the
turn's final message: the agent loop ends after the marker instead of looping
back to generate.
"""
import logging

from langchain_core.tools import tool

from app.pipelines.tools.base import ToolResultHandler, ToolSpec

logger = logging.getLogger(__name__)

DRAFT_TOOL_NAME = "draft_document"


class DraftHandler(ToolResultHandler):
    """Persist only the document title (for the chip label / file name). The
    marker itself is the assistant row's tool call; nothing heavy is stored."""

    async def capture(self, *, args: dict, content: str) -> dict:
        return {"title": (args.get("title") or "Документ").strip() or "Документ"}

    async def run(self, *, args: dict, state: dict) -> str:
        title = state.get("title") or "документ"
        return f"[К ответу приложен документ «{title}» для скачивания.]"


def drafting_tool_specs() -> list[ToolSpec]:
    """Build the terminal drafting marker tool. No LLM/segmenter needed: the
    actual formatting happens on chip click in the render endpoint."""

    @tool
    async def draft_document(title: str) -> str:
        """Прикрепить к ответу скачиваемый файл .docx с подготовленным документом.

        Вызывай этот инструмент, когда твой ответ содержит ГОТОВЫЙ процессуальный
        документ (иск, возражения, ходатайство, пояснения, жалобу), который юрист
        захочет получить файлом для подачи в суд. Сам текст документа изложи в
        тексте ответа как обычно — инструмент лишь добавит к ответу кнопку
        скачивания .docx, свёрстанного по стандарту для суда.

        title — краткое название документа (для имени файла и подписи кнопки),
        например «Возражения на исковое заявление».
        """
        return f"ok:{(title or 'Документ').strip()}"

    return [ToolSpec(draft_document, DraftHandler(), terminal=True)]
