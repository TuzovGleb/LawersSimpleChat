"""Turn drafted legal-document text into render-ready typed blocks (variant C).

The drafting LLM writes the document as plain text (one logical paragraph per
line; tables as ``|``-delimited rows; borderless two-column blocks as
``||``-delimited rows). We then:

1. ``tokenize_draft`` — deterministically split that text into ordered units:
   ``spacer`` / ``table`` (both detected in code, incl. borderless ``||``
   columns) and ``content`` lines (each given a sequential id).
2. ``classify_lines`` — a cheap LLM call that labels each content line id with a
   block type (TYPES ONLY — the document text is never re-emitted, unlike a full
   segmentation pass) and names the file.
3. ``assemble_blocks`` — reassemble units + per-id types into the block dicts
   ``docx_builder.render_docx`` lays out.

Text hygiene (nbsp, ёлочки) lives in the renderer, not here.
"""
from __future__ import annotations

import logging
import re
from typing import Literal, get_args

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, field_validator

from app.rag_core.llm import (
    ChatProviderParams,
    ModelConfig,
    WebSearchConfig,
    build_chat_llm,
)

logger = logging.getLogger(__name__)


def build_segmenter_llm(params: ChatProviderParams) -> ChatOpenAI:
    """A deterministic LLM for line classification: temp 0, no web-search.

    Prefers the cheap ``lite`` model when configured — the task is labelling
    numbered lines with one of 12 types (types only, no text is re-emitted), so
    the default chat model is overkill in both latency and cost."""
    base = params.models.get("lite") or params.models[params.default_model]
    return build_chat_llm(
        params.provider,
        ModelConfig(
            name=base.name,
            temperature=0,
            max_tokens=16000,
            web_search=WebSearchConfig(enabled=False),
            provider_order=base.provider_order,
        ),
    )


def build_drafting_llm(params: ChatProviderParams) -> ChatOpenAI:
    """LLM for drafting a full legal document from chat context: default
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
            provider_order=base.provider_order,
            # No cache markup: drafting fires rarely and its system message
            # embeds the serialized chat history, so the prompt is one-shot —
            # a cache write (1.25x, or 2x with the system breakpoint's 1h TTL)
            # on a ~100k-token prompt would never be read back.
            caching="off",
        ),
    )

# Content block types the classifier assigns per line. ``spacer`` and ``table``
# are NOT here — they are detected deterministically in tokenize_draft.
ContentType = Literal[
    "header", "title", "subtitle", "h1", "body", "quote",
    "proshu", "item", "annex_h", "annex_item", "sign_left", "sign_right",
]


# ---- 1. deterministic tokenizer (no LLM) ------------------------------------

_MD_HEADING = re.compile(r"^#{1,6}\s+")
# Горизонтальная линейка markdown. НЕ матчим _{3,}: строка из подчёркиваний —
# это прочерк-плейсхолдер («____________»), а не разметка.
_MD_HR = re.compile(r"^(?:-{3,}|\*{3,})$")


def _strip_markdown(line: str) -> str:
    """Детерминированная зачистка markdown-остатков. Промпт драфтера запрещает
    разметку, но одно нарушение не должно попадать звёздочками в .docx.
    Правила сужены, чтобы не портить легитимный текст: ``**`` снимается только
    ПАРОЙ вокруг текста (одиночное «2**10» не трогаем), ведущий ``>`` — только
    перед словом (сравнение «> 50 %» сохраняется); ``__`` и одиночные ``*``/``_``
    не трогаем — коллизия с плейсхолдерами."""
    line = _MD_HEADING.sub("", line)
    line = re.sub(r"^>+\s+(?=[^\W\d])", "", line)
    line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
    return line.replace("`", "").strip()


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


def _split_columns(line: str) -> list[str]:
    """Cells of a borderless two-column row (``left || right``). Edge cells are
    KEPT even when empty — «Директор ____ ||» means "left column only", and the
    empty right cell preserves the two-column shape. A single markdown-style
    edge pipe («| а || б |») is trimmed off the outer cells."""
    cells = [c.strip() for c in line.split("||")]
    if cells and cells[0].startswith("|"):
        cells[0] = cells[0][1:].strip()
    if cells and cells[-1].endswith("|"):
        cells[-1] = cells[-1][:-1].strip()
    return cells


def _is_separator_columns_row(cells: list[str]) -> bool:
    """Markdown separator in a ``||`` run (``|---||---|``): all NON-empty cells
    are dashes/colons (empty edge cells are legal in columns rows)."""
    non_empty = [c for c in cells if c]
    return bool(non_empty) and all(set(c) <= set("-:") for c in non_empty)


def tokenize_draft(text: str) -> list[dict]:
    """Split drafted text into ordered units (deterministic, no LLM).

    - blank line(s) -> a single ``spacer`` (consecutive blanks collapsed);
    - a run of ``||``-delimited rows -> one borderless-columns ``table``
      (checked BEFORE ``|``: the double pipe contains a single one; a single
      ``||`` row is enough — the marker is deliberate, unlike a stray ``|``);
    - a run of ``|``-delimited rows -> one ``table`` (cells parsed; markdown
      separator rows dropped);
    - any other non-empty line -> a ``content`` unit with a sequential id.

    Relies on the drafting prompt's rule "one logical paragraph = one line".
    """
    units: list[dict] = []
    run_lines: list[str] = []
    run_kind: str | None = None  # "table" | "columns"
    next_id = 0
    prev_spacer = False

    def flush_run() -> None:
        nonlocal run_lines, run_kind, next_id
        if not run_lines:
            run_kind = None
            return
        if run_kind == "columns":
            rows = [
                cells for cells in (_split_columns(line) for line in run_lines)
                if not _is_separator_columns_row(cells)
            ]
            if rows:
                units.append({"kind": "table", "borderless": True, "rows": rows})
            else:
                # Ран из одних markdown-сепараторов: не терять строки молча.
                for line in run_lines:
                    next_id += 1
                    units.append({"kind": "content", "id": next_id, "text": line})
        else:
            rows = [
                _split_cells(line) for line in run_lines
                if not _is_separator_row(_split_cells(line))
            ]
            # A real table has >= 2 rows (header + data). A lone '|'-line is
            # just prose that happened to contain a pipe -> content instead.
            if len(rows) >= 2:
                units.append({"kind": "table", "rows": rows})
            else:
                for line in run_lines:
                    next_id += 1
                    units.append({"kind": "content", "id": next_id, "text": line})
        run_lines = []
        run_kind = None

    for raw in text.split("\n"):
        line = raw.strip()
        if line and not _MD_HR.match(line):
            line = _strip_markdown(line)
        else:
            line = ""  # пустая строка или markdown-линейка -> spacer
        if not line:
            flush_run()
            if not prev_spacer:
                units.append({"kind": "spacer"})
                prev_spacer = True
            continue
        prev_spacer = False
        if "||" in line:
            kind = "columns"
        elif _looks_like_table_row(line):
            kind = "table"
        else:
            kind = None
        if kind is not None:
            # Смена вида ряда («||»-блок вплотную к «|»-таблице) режет ран:
            # колонки и таблица не должны склеиваться в один блок.
            if run_kind is not None and run_kind != kind:
                flush_run()
            run_kind = kind
            run_lines.append(line)
            continue
        flush_run()
        next_id += 1
        units.append({"kind": "content", "id": next_id, "text": line})
    flush_run()

    while units and units[0]["kind"] == "spacer":
        units.pop(0)
    while units and units[-1]["kind"] == "spacer":
        units.pop()
    return units


# ---- 2. cheap LLM classifier (types only, no text re-emitted) ----------------

_CONTENT_TYPES: frozenset[str] = frozenset(get_args(ContentType))


class LineType(BaseModel):
    id: int = Field(description="Номер строки из входа.")
    type: ContentType = Field(description="Тип блока для этой строки.")

    @field_validator("type", mode="before")
    @classmethod
    def _coerce_unknown_type(cls, v):
        # Один невалидный тип от LLM не должен валить pydantic-валидацию всего
        # ответа (и разметку всего документа) — деградирует только эта строка.
        return v if v in _CONTENT_TYPES else "body"


class ClassifiedDoc(BaseModel):
    # default="": поле нужно только для наблюдаемости — его отсутствие в ответе
    # LLM не должно валить валидацию и деградировать разметку всего документа.
    family: str = Field(
        default="",
        description=(
            "Вид документа одним словом: процессуальный / договор / претензия / "
            "заключение / письмо / доверенность / приказ / протокол / иной."
        ),
    )
    file_name: str = Field(
        description=(
            "Короткое имя файла БЕЗ расширения, отражающее суть документа "
            "(например «Возражения на исковое заявление», «Договор поставки», "
            "«Правовое заключение»). Без даты и № дела."
        )
    )
    lines: list[LineType] = Field(description="Тип для каждой пронумерованной строки.")


CLASSIFIER_PROMPT = """\
Ты — классификатор абзацев готового юридического документа: процессуального \
(иск, возражения, ходатайство, жалоба) или иного (договор, претензия, \
заключение, письмо, доверенность, приказ, протокол). На вход — пронумерованные \
строки уже написанного документа (одна логическая строка/абзац на номер). Для \
КАЖДОГО номера верни тип блока (ТОЛЬКО тип, текст возвращать и менять не \
нужно), плюс вид документа (family) и короткое имя файла.

Типы:
- header — строка шапки-адресата в НАЧАЛЕ документа. Для суда: суд, № дела, \
истец/ответчик/третье лицо, представитель, адрес, телефон, e-mail. Для \
претензии и письма: «Кому: …», «От: …», должность/организация/ФИО адресата, \
адрес. Гриф «УТВЕРЖДЕНО/УТВЕРЖДАЮ …» — тоже header. Каждая такая строка — header.
- title — основной заголовок документа (обычно ПРОПИСНЫМИ: ВОЗРАЖЕНИЯ, \
ИСКОВОЕ ЗАЯВЛЕНИЕ, ДОГОВОР ПОСТАВКИ, ПРЕТЕНЗИЯ, ДОВЕРЕННОСТЬ, ПРИКАЗ).
- subtitle — подзаголовок под заголовком («на кассационную жалобу …», «о …»), \
а также обращение в письме («Уважаемый Иван Иванович!»).
- h1 — заголовок раздела: короткая строка-название («I. ПРАВОВАЯ ПОЗИЦИЯ», \
«1. ПРЕДМЕТ ДОГОВОРА», «Реквизиты и подписи сторон», «ПОВЕСТКА ДНЯ», \
«СЛУШАЛИ:», «ВЫСТУПИЛИ:»).
- body — обычный абзац текста.
- quote — дословная цитата судебной практики или нормы закона.
- proshu — резолютивная строка со словом-командой ПРОПИСНЫМИ перед двоеточием: \
«На основании изложенного, руководствуясь …, ПРОШУ:», «…ТРЕБУЮ:», \
«ПРИКАЗЫВАЮ:», «РЕШИЛИ:», «ПОСТАНОВИЛИ:».
- item — пункт требования или нумерованного списка (1. …, 2. …).
- annex_h — слово «Приложения:».
- annex_item — отдельный пункт списка приложений.
- sign_left — дата или строка подписи слева.
- sign_right — подпись справа (как правило, ФИО представителя).

Уточнения по спорным строкам:
- Строка вида «N.N. …» (1.1., 2.3.4.) с текстом-предложением — это ПУНКТ документа: body, НЕ h1. h1 — только короткое название раздела без текста после него.
- Вводный абзац сразу после заголовка («В производстве … находится дело …») — это body, не subtitle.
- Строка места/даты перед подписью («г. Москва, «__» января 2026 г.») — sign_left; дата внутри обычного абзаца типом не выделяется. Строка места и даты в НАЧАЛЕ документа (например, дата прописью в доверенности) — body.
- «Исх. № … от …» в письме — sign_left (стоит слева), НЕ header.
- «ПРОШУ:» (в том числе «На основании изложенного, … ПРОШУ:») — proshu; следующие за ней нумерованные требования — item. То же для «ТРЕБУЮ:» претензии и «ПРИКАЗЫВАЮ:»/«РЕШИЛИ:» приказа и протокола.
- Цена иска, размер госпошлины, ИНН/СНИЛС/ОГРН и прочие реквизиты в блоке ДО заголовка документа — header. Но реквизиты сторон в КОНЦЕ договора (после «Реквизиты и подписи сторон») — body, НЕ header.
- «С уважением,» и следующая за ней строка должности/ФИО — обе sign_left (это НЕ пара слева/справа).
- Если строка не подходит ни под один тип — body.

Верни family, file_name и по одному элементу {id, type} на КАЖДЫЙ номер входа — не пропускай номера и не добавляй лишних.\
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
        extra={
            "line_count": len(content_units),
            "file_name": result.file_name,
            # Наблюдаемость: какие жанры реально просят файлом. На маршрутизацию
            # не влияет — вид документа выбирает драфтер по контексту.
            "doc_family": result.family,
        },
    )
    return result


# ---- 3. deterministic reassembler -------------------------------------------

# Flowing-text block types: an empty paragraph between two of these is just
# noise (the renderer already spaces paragraphs), so it is dropped. Spacers
# next to a structural block (header/title/h1/proshu/sign/…) are kept.
_FLOW_TYPES = {"body", "item", "quote", "annex_item"}

# Пункт с многоуровневым номером и текстом-предложением («1.1. Поставщик
# обязуется …»). Вероятностный lite-классификатор регулярно принимает такие
# строки за h1 — детерминированная страховка возвращает их в body, чтобы
# полдоговора не отрендерилось жирными заголовками.
_NUMBERED_SENTENCE = re.compile(r"^\d+(?:\.\d+)+\.?\s+.+[.;:!?…]$")


def assemble_blocks(units: list[dict], types_by_id: dict[int, str]) -> list[dict]:
    """Reassemble ordered units + per-id types into render-ready block dicts.

    A content line with no (or invalid) classification falls back to ``body``.
    An ``h1`` on a numbered-sentence line is downgraded to ``body`` (see
    ``_NUMBERED_SENTENCE``). Empty paragraphs sandwiched between two
    flowing-text paragraphs are dropped (a deterministic guard against the
    model over-blank-lining its draft).
    """
    blocks: list[dict] = []
    for unit in units:
        kind = unit["kind"]
        if kind == "spacer":
            blocks.append({"type": "spacer", "text": ""})
        elif kind == "table":
            block = {"type": "table", "text": "", "rows": [{"cells": row} for row in unit["rows"]]}
            if unit.get("borderless"):
                block["borderless"] = True
            blocks.append(block)
        else:  # content
            btype = types_by_id.get(unit["id"], "body")
            if btype == "h1" and _NUMBERED_SENTENCE.match(unit["text"]):
                btype = "body"
            blocks.append({"type": btype, "text": unit["text"]})

    cleaned: list[dict] = []
    for i, block in enumerate(blocks):
        if block["type"] == "spacer":
            prev_type = blocks[i - 1]["type"] if i > 0 else None
            next_type = blocks[i + 1]["type"] if i + 1 < len(blocks) else None
            if prev_type in _FLOW_TYPES and next_type in _FLOW_TYPES:
                continue  # drop noise spacer between flowing paragraphs
        cleaned.append(block)
    return cleaned
