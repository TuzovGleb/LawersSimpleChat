"""Turn drafted procedural text into render-ready typed blocks (variant C).

The drafting LLM writes the document as plain text (one logical paragraph per
line; tables as ``|``-delimited rows). We then:

1. ``tokenize_draft`` — deterministically split that text into ordered units:
   ``spacer`` / ``table`` (both detected in code) and ``content`` lines (each
   given a sequential id).
2. ``classify_lines`` — a cheap LLM call that labels each content line id with a
   block type (TYPES ONLY — the document text is never re-emitted, unlike a full
   segmentation pass) and names the file.
3. ``assemble_blocks`` — reassemble units + per-id types into the block dicts
   ``docx_builder.render_docx`` lays out.

Text hygiene (nbsp, ёлочки) lives in the renderer, not here.
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


def build_drafting_llm(params: ChatProviderParams) -> ChatOpenAI:
    """LLM for drafting a full procedural document from chat context: default
    model, low temperature for precise legal prose, large output budget, no
    web-search (it drafts from the supplied context, not the internet)."""
    base = params.models[params.default_model]
    return build_chat_llm(
        params.provider,
        ModelConfig(
            name=base.name,
            temperature=0.2,
            max_tokens=32000,
            web_search=WebSearchConfig(enabled=False),
        ),
    )

# Content block types the classifier assigns per line. ``spacer`` and ``table``
# are NOT here — they are detected deterministically in tokenize_draft.
ContentType = Literal[
    "header", "title", "subtitle", "h1", "body", "quote",
    "proshu", "item", "annex_h", "annex_item", "sign_left", "sign_right",
]


# ---- 1. deterministic tokenizer (no LLM) ------------------------------------

def _split_cells(line: str) -> list[str]:
    parts = [c.strip() for c in line.split("|")]
    # Drop empty edge cells produced by a leading/trailing pipe.
    if parts and parts[0] == "":
        parts = parts[1:]
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return parts


def _is_separator_row(cells: list[str]) -> bool:
    """Markdown table separator, e.g. ``|---|:--:|``."""
    return bool(cells) and all(c and set(c) <= set("-:") for c in cells)


def _looks_like_table_row(line: str) -> bool:
    # Legal prose practically never contains '|', and the drafting prompt
    # reserves '|' for tables, so requiring >= 2 cells is a safe guard.
    return "|" in line and len(_split_cells(line)) >= 2


def tokenize_draft(text: str) -> list[dict]:
    """Split drafted text into ordered units (deterministic, no LLM).

    - blank line(s) -> a single ``spacer`` (consecutive blanks collapsed);
    - a run of ``|``-delimited rows -> one ``table`` (cells parsed; markdown
      separator rows dropped);
    - any other non-empty line -> a ``content`` unit with a sequential id.

    Relies on the drafting prompt's rule "one logical paragraph = one line".
    """
    units: list[dict] = []
    table_lines: list[str] = []
    next_id = 0
    prev_spacer = False

    def flush_table() -> None:
        nonlocal table_lines, next_id
        if not table_lines:
            return
        rows = [
            _split_cells(line) for line in table_lines
            if not _is_separator_row(_split_cells(line))
        ]
        # A real table has >= 2 rows (header + data). A lone '|'-line is just
        # prose that happened to contain a pipe -> emit it as content instead.
        if len(rows) >= 2:
            units.append({"kind": "table", "rows": rows})
        else:
            for line in table_lines:
                next_id += 1
                units.append({"kind": "content", "id": next_id, "text": line})
        table_lines = []

    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            flush_table()
            if not prev_spacer:
                units.append({"kind": "spacer"})
                prev_spacer = True
            continue
        prev_spacer = False
        if _looks_like_table_row(line):
            table_lines.append(line)
            continue
        flush_table()
        next_id += 1
        units.append({"kind": "content", "id": next_id, "text": line})
    flush_table()

    while units and units[0]["kind"] == "spacer":
        units.pop(0)
    while units and units[-1]["kind"] == "spacer":
        units.pop()
    return units


# ---- 2. cheap LLM classifier (types only, no text re-emitted) ----------------

class LineType(BaseModel):
    id: int = Field(description="Номер строки из входа.")
    type: ContentType = Field(description="Тип блока для этой строки.")


class ClassifiedDoc(BaseModel):
    file_name: str = Field(
        description=(
            "Короткое имя файла БЕЗ расширения, отражающее суть документа "
            "(например «Возражения на исковое заявление»). Без даты и № дела."
        )
    )
    lines: list[LineType] = Field(description="Тип для каждой пронумерованной строки.")


CLASSIFIER_PROMPT = """\
Ты — классификатор абзацев готового процессуального документа для российского \
суда. На вход — пронумерованные строки уже написанного документа (одна логическая \
строка/абзац на номер). Для КАЖДОГО номера верни тип блока (ТОЛЬКО тип, текст \
возвращать и менять не нужно) плюс короткое имя файла.

Типы:
- header — строка шапки: суд, № дела, истец/ответчик/третье лицо, представитель, \
адрес, телефон, e-mail. Каждая такая строка — header.
- title — основной заголовок документа (обычно ПРОПИСНЫМИ: ВОЗРАЖЕНИЯ, \
ИСКОВОЕ ЗАЯВЛЕНИЕ, ХОДАТАЙСТВО).
- subtitle — подзаголовок под заголовком («на кассационную жалобу …», «о …»).
- h1 — заголовок раздела (I., II., 1.1., «ПРАВОВАЯ ПОЗИЦИЯ …»).
- body — обычный абзац текста.
- quote — дословная цитата судебной практики или нормы закона.
- proshu — просительная часть со словом ПРОШУ («На основании изложенного, \
руководствуясь …, ПРОШУ:»).
- item — пункт требования или нумерованного списка (1. …, 2. …).
- annex_h — слово «Приложения:».
- annex_item — отдельный пункт списка приложений.
- sign_left — дата или строка подписи слева.
- sign_right — подпись справа (как правило, ФИО представителя).

Верни file_name и по одному элементу {id, type} на каждый номер входа.\
"""


async def classify_lines(llm: ChatOpenAI, content_units: list[dict]) -> ClassifiedDoc:
    """Label each numbered content line with a block type (cheap: types only)."""
    numbered = "\n".join(f'{unit["id"]}: {unit["text"]}' for unit in content_units)
    structured = llm.with_structured_output(ClassifiedDoc)
    result: ClassifiedDoc = await structured.ainvoke(
        [
            {"role": "system", "content": CLASSIFIER_PROMPT},
            {"role": "user", "content": numbered},
        ]
    )
    logger.info(
        "Document lines classified",
        extra={"line_count": len(content_units), "file_name": result.file_name},
    )
    return result


# ---- 3. deterministic reassembler -------------------------------------------

# Flowing-text block types: an empty paragraph between two of these is just
# noise (the renderer already spaces paragraphs), so it is dropped. Spacers
# next to a structural block (header/title/h1/proshu/sign/…) are kept.
_FLOW_TYPES = {"body", "item", "quote", "annex_item"}


def assemble_blocks(units: list[dict], types_by_id: dict[int, str]) -> list[dict]:
    """Reassemble ordered units + per-id types into render-ready block dicts.

    A content line with no (or invalid) classification falls back to ``body``.
    Empty paragraphs sandwiched between two flowing-text paragraphs are dropped
    (a deterministic guard against the model over-blank-lining its draft).
    """
    blocks: list[dict] = []
    for unit in units:
        kind = unit["kind"]
        if kind == "spacer":
            blocks.append({"type": "spacer", "text": ""})
        elif kind == "table":
            blocks.append(
                {"type": "table", "text": "", "rows": [{"cells": row} for row in unit["rows"]]}
            )
        else:  # content
            blocks.append({"type": types_by_id.get(unit["id"], "body"), "text": unit["text"]})

    cleaned: list[dict] = []
    for i, block in enumerate(blocks):
        if block["type"] == "spacer":
            prev_type = blocks[i - 1]["type"] if i > 0 else None
            next_type = blocks[i + 1]["type"] if i + 1 < len(blocks) else None
            if prev_type in _FLOW_TYPES and next_type in _FLOW_TYPES:
                continue  # drop noise spacer between flowing paragraphs
        cleaned.append(block)
    return cleaned
