"""Stage 1 of the drafting tool: segment raw procedural text into typed blocks.

An LLM turns a (often AI-generated, hand-wrapped) legal document into a clean
list of ``Block``s that ``docx_builder.render_docx`` can lay out. The model only
*reformats* — it must not change the legal meaning or invent requisites. The
block taxonomy and text-hygiene rules mirror the ``legal-docx-formatter`` skill.
"""
from __future__ import annotations

import logging
from typing import Literal

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from app.rag_core.llm import (
    ChatProviderParams,
    ModelConfig,
    WebSearchConfig,
    build_chat_llm,
)

logger = logging.getLogger(__name__)


def build_segmenter_llm(params: ChatProviderParams) -> ChatOpenAI:
    """A deterministic LLM for segmentation: default model, temp 0, no web-search,
    larger output budget (a long document is echoed back as JSON blocks)."""
    base = params.models[params.default_model]
    return build_chat_llm(
        params.provider,
        ModelConfig(
            name=base.name,
            temperature=0,
            max_tokens=32000,
            web_search=WebSearchConfig(enabled=False),
        ),
    )

BlockType = Literal[
    "header", "title", "subtitle", "h1", "body", "quote", "proshu",
    "item", "annex_h", "annex_item", "sign_left", "sign_right", "spacer", "table",
]


class TableRow(BaseModel):
    """Одна строка таблицы."""

    cells: list[str] = Field(description="Ячейки строки слева направо.")


class Block(BaseModel):
    """Один блок документа с типом, определяющим вёрстку."""

    type: BlockType = Field(description="Тип блока (определяет вёрстку).")
    text: str = Field(
        default="",
        description="Текст блока. Для spacer и table — пустая строка.",
    )
    rows: list[TableRow] | None = Field(
        default=None,
        description=(
            "Только для type='table': строки таблицы по порядку. Первая строка — "
            "заголовок (будет выделена жирным). У всех строк одинаковое число ячеек. "
            "Для остальных типов — null."
        ),
    )


class DraftedDocument(BaseModel):
    """Сегментированный документ + короткое имя файла."""

    file_name: str = Field(
        description=(
            "Короткое имя файла БЕЗ расширения, отражающее суть документа, "
            "например «Возражения на исковое заявление» или «Ходатайство об "
            "отложении заседания». Без даты и номера дела."
        )
    )
    blocks: list[Block] = Field(
        description="Документ, разбитый на типизированные блоки, по порядку."
    )


SYSTEM_PROMPT = """\
Ты — форматтер процессуальных документов для российского суда. На вход тебе \
дают сырой текст юридического документа (иск, возражения, ходатайство, \
пояснения, жалоба), часто сгенерированный ИИ и с «съехавшей» вёрсткой: ручными \
переносами строк, выравниванием пробелами, разорванными предложениями.

Твоя задача — РАЗБИТЬ этот текст на список типизированных блоков (segmentation), \
ничего не дописывая и не меняя по смыслу. Ты НЕ пишешь документ заново и НЕ \
меняешь правовую суть — только приводишь форму в порядок.

Жёсткие правила:
1. Один логический абзац = один блок. Если предложение разорвано на строки \
ручными переносами — склей в один абзац, текст переносит сам Word.
2. НИКОГДА не верстай пробелами или табами. Положение шапки/подписи задаётся \
ТОЛЬКО типом блока (header, sign_left, sign_right), а не отступами из пробелов. \
Убери из текста ведущие пробельные «лесенки».
3. Не выдумывай реквизиты (номер дела, адрес, ФИО). Если их нет — оставь как в \
исходнике или поставь плейсхолдер ____________.
4. Не меняй содержание и формулировки. Разрешено только: склейка строк в абзацы, \
снятие пробельной вёрстки, очевидные опечатки пробелов.

Типы блоков:
- header — строка шапки: суд, № дела, истец, ответчик, представитель, адрес. \
Каждая логическая строка шапки — отдельный блок header.
- title — основной заголовок документа (обычно ПРОПИСНЫМИ: ВОЗРАЖЕНИЯ, \
ИСКОВОЕ ЗАЯВЛЕНИЕ, ХОДАТАЙСТВО).
- subtitle — подзаголовок под заголовком («на возражения ответчика…», «о …»).
- h1 — заголовок раздела (I., II., 1.1., «ПРАВОВАЯ ПОЗИЦИЯ …»).
- body — обычный абзац текста.
- quote — дословная цитата судебной практики или нормы закона (её движок \
обернёт в «ёлочки»; кавычки внутри можно не ставить).
- proshu — абзац с просительной частью, содержащий слово ПРОШУ \
(«На основании изложенного, руководствуясь …, ПРОШУ:»).
- item — отдельный пункт требования или нумерованного списка (1. …, 2. …).
- table — таблица (расчёт процентов/задолженности, реестр, сравнительная \
таблица). Если в исходнике таблица в markdown («| … | … |») или ASCII-разметкой — \
ВСЕГДА оформляй её как блок type='table' и заполняй rows (НЕ как body-текст). \
Каждая строка rows — это её ячейки слева направо; первая строка rows — заголовок. \
У всех строк одинаковое число ячеек (недостающие — пустая строка). Текст не \
дублируй в поле text (оставь пустым). Разделители-строки markdown («|---|---|») \
пропускай.
- annex_h — слово «Приложения:».
- annex_item — отдельный пункт списка приложений.
- sign_left — дата или строка подписи слева.
- sign_right — подпись справа (как правило, ФИО представителя).
- spacer — пустой абзац-отступ между смысловыми блоками (шапка/заголовок/тело/\
подпись). Текст пустой. Не злоупотребляй: spacer только между крупными частями.

Верни file_name и список blocks по порядку.\
"""


async def segment_to_blocks(llm: ChatOpenAI, raw_text: str) -> DraftedDocument:
    """Run the LLM segmentation and return the validated structured result."""
    structured = llm.with_structured_output(DraftedDocument)
    result: DraftedDocument = await structured.ainvoke(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": raw_text},
        ]
    )
    logger.info(
        "Document segmented into blocks",
        extra={"block_count": len(result.blocks), "file_name": result.file_name},
    )
    return result
