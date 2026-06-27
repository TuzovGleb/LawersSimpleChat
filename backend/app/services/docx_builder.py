"""Deterministic .docx assembly for Russian procedural documents.

Stage 2 of the drafting tool: takes a list of typed blocks
``[{"type": "...", "text": "..."}]`` and renders a fully formatted .docx with
the court-document layout baked in (A4, margins 3/1.5/2/2 cm, Times New Roman
12pt, 1.5 line spacing, justified body, 1.25 cm first-line indent).

Ported from the standalone ``build_docx.py`` skill engine; the only changes are
that ``render_docx`` returns ``bytes`` (via an in-memory buffer) instead of
writing to disk, and the CLI wrapper is dropped. Depends only on ``python-docx``
(already a backend dependency), so it runs in-process — no sandbox/CLI needed.

Block types (``type``):
    header      — строка шапки (суд, дело, истец/ответчик, адрес). Слева, без отступа.
    title       — заголовок документа. По центру, жирный.
    subtitle    — подзаголовок под title. По центру, жирный.
    h1          — заголовок раздела (I., II., 1.1.). Жирный, слева, отступ сверху.
    body        — обычный абзац. По ширине, красная строка 1.25.
    quote       — цитата практики. Как body, принудительно в «ёлочках».
    proshu      — абзац "...ПРОШУ:" (слово ПРОШУ — жирным).
    item        — пункт требования/списка. Как body.
    annex_h     — слово "Приложения:" (жирный, слева).
    annex_item  — пункт приложения. Как body.
    sign_left   — строка подписи/даты слева.
    sign_right  — строка подписи справа.
    spacer      — пустой абзац (пустая строка-отступ).
"""
from __future__ import annotations

import re
from io import BytesIO

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

FONT_NAME = "Times New Roman"
FONT_SIZE = Pt(12)
FIRST_LINE = Cm(1.25)
LINE_SPACING = 1.5

NBSP = " "  # неразрывный пробел
NDASH = "–"
MDASH = "—"

# Сокращения, после которых пробел перед числом должен быть неразрывным.
ABBR_BEFORE_NUM = [
    "№", "§", "ст", "стт", "п", "пп", "абз", "ч", "гл", "разд", "р", "л",
    "д", "дд", "т", "кв", "оф", "пом", "стр", "г", "гг", "обл", "ул", "пл",
]
# Короткие слова-предлоги, которые "тянут" следующий токен.
SHORT_WORDS = ["в", "во", "и", "к", "с", "со", "о", "об", "от", "до", "по",
               "на", "за", "из", "у", "не", "ни", "а", "но"]


def normalize_text(t: str) -> str:
    """Текстовая гигиена: кавычки, тире, неразрывные пробелы, лишние пробелы."""
    if not t:
        return t
    # 1. Убираем мягкие переносы и табы, схлопываем пробелы.
    t = t.replace("­", "").replace("\t", " ")
    t = re.sub(r"[  ]{2,}", " ", t)
    t = t.strip()

    # 2. Прямые кавычки -> ёлочки (парная расстановка).
    t = re.sub(r'"([^"]*)"', lambda m: "«" + m.group(1) + "»", t)
    t = t.replace('"', "")

    # 3. Дефис между пробелами -> длинное тире.
    t = re.sub(r"\s[-–]\s", f"{NBSP}{MDASH} ", t)

    # 4. Пробел перед знаком препинания убрать.
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)

    # 5. Неразрывный пробел после сокращений перед числом: "ст. 196" -> "ст. 196".
    abbr = "|".join(sorted(ABBR_BEFORE_NUM, key=len, reverse=True))
    t = re.sub(rf"\b({abbr})\.\s+(?=[\dIVXЛ№])", rf"\1.{NBSP}", t, flags=re.IGNORECASE)
    t = re.sub(r"([№§])\s+", rf"\1{NBSP}", t)

    # 6. Инициалы: "О. В. Пашкин" и "Пашкин О. В." — неразрывно.
    t = re.sub(r"\b([А-ЯЁ])\.\s*([А-ЯЁ])\.\s*([А-ЯЁ][а-яё]+)",
               rf"\1.{NBSP}\2.{NBSP}\3", t)
    t = re.sub(r"\b([А-ЯЁ][а-яё]+)\s+([А-ЯЁ])\.\s*([А-ЯЁ])\.",
               rf"\1{NBSP}\2.{NBSP}\3.", t)

    # 7. Число + единица/слово, которые нельзя отрывать: "2026 г.", "100 руб.".
    t = re.sub(r"(\d)\s+(г|гг|руб|коп|млн|млрд|тыс|руб\.|%)\b", rf"\1{NBSP}\2", t)

    # 8. "ГК РФ", "ГПК РФ" и т.п. — неразрывно.
    t = re.sub(r"\b(ГК|ГПК|АПК|УК|УПК|КоАП|НК|ТК|СК)\s+РФ\b", rf"\1{NBSP}РФ", t)

    # 9. Короткие слова-предлоги приклеиваем к следующему слову.
    sw = "|".join(SHORT_WORDS)
    t = re.sub(rf"(^|\s)({sw})\s+", lambda m: f"{m.group(1)}{m.group(2)}{NBSP}", t,
               flags=re.IGNORECASE)

    t = re.sub(r" {2,}", " ", t)
    return t


def _set_run_font(run, bold: bool = False) -> None:
    run.font.name = FONT_NAME
    run.font.size = FONT_SIZE
    run.bold = bold
    # Критично для кириллицы — задать шрифт во всех слотах rFonts.
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(attr), FONT_NAME)


def _base_format(p, align, first_line: bool = True, space_before: int = 0,
                 space_after: int = 0) -> None:
    pf = p.paragraph_format
    pf.alignment = align
    pf.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
    pf.line_spacing = LINE_SPACING
    pf.space_before = Pt(space_before)
    pf.space_after = Pt(space_after)
    pf.first_line_indent = FIRST_LINE if first_line else None
    pf.left_indent = None


def _add_paragraph(doc, text, align, bold: bool = False, first_line: bool = True,
                   space_before: int = 0, space_after: int = 0, bold_word=None):
    p = doc.add_paragraph()
    _base_format(p, align, first_line, space_before, space_after)
    text = normalize_text(text)
    if bold_word and bold_word in text:
        # Делим абзац, чтобы выделить одно слово (например ПРОШУ).
        idx = text.index(bold_word)
        before, word, after = text[:idx], bold_word, text[idx + len(bold_word):]
        if before:
            _set_run_font(p.add_run(before), bold)
        _set_run_font(p.add_run(word), True)
        if after:
            _set_run_font(p.add_run(after), bold)
    else:
        _set_run_font(p.add_run(text), bold)
    return p


def _setup_page(doc) -> None:
    sec = doc.sections[0]
    sec.orientation = WD_ORIENT.PORTRAIT
    sec.page_width = Cm(21.0)
    sec.page_height = Cm(29.7)
    sec.left_margin = Cm(3.0)
    sec.right_margin = Cm(1.5)
    sec.top_margin = Cm(2.0)
    sec.bottom_margin = Cm(2.0)
    st = doc.styles["Normal"]
    st.font.name = FONT_NAME
    st.font.size = FONT_SIZE
    st.element.rPr.rFonts.set(qn("w:eastAsia"), FONT_NAME)
    st.element.rPr.rFonts.set(qn("w:cs"), FONT_NAME)


_J = WD_ALIGN_PARAGRAPH.JUSTIFY
_C = WD_ALIGN_PARAGRAPH.CENTER
_L = WD_ALIGN_PARAGRAPH.LEFT
_R = WD_ALIGN_PARAGRAPH.RIGHT


def _add_table(doc, rows: list[dict]) -> None:
    """Render a bordered Word table from ``[{"cells": [...]}, ...]``.

    First row is treated as a bold header. Ragged rows are padded to the widest
    row so python-docx never indexes out of range. Cells use the document font
    and single spacing for compactness; text goes through ``normalize_text``.
    """
    matrix = [
        list(r.get("cells", []) or [])
        for r in rows
        if isinstance(r, dict)
    ]
    if not matrix:
        return
    cols = max((len(r) for r in matrix), default=0)
    if cols == 0:
        return

    table = doc.add_table(rows=len(matrix), cols=cols)
    table.style = "Table Grid"  # single-line borders on every cell
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True
    for r_idx, row in enumerate(matrix):
        for c_idx in range(cols):
            cell = table.cell(r_idx, c_idx)
            txt = normalize_text(row[c_idx]) if c_idx < len(row) else ""
            para = cell.paragraphs[0]
            pf = para.paragraph_format
            pf.line_spacing_rule = WD_LINE_SPACING.SINGLE
            pf.space_before = Pt(0)
            pf.space_after = Pt(0)
            pf.first_line_indent = None
            _set_run_font(para.add_run(txt), bold=(r_idx == 0))


def render_docx(blocks: list[dict]) -> bytes:
    """Render a list of typed blocks into a .docx and return its bytes."""
    doc = Document()
    _setup_page(doc)
    for b in blocks:
        t = b.get("type", "body")
        txt = b.get("text", "")
        if t == "header":
            _add_paragraph(doc, txt, _L, bold=False, first_line=False)
        elif t == "title":
            _add_paragraph(doc, txt, _C, bold=True, first_line=False,
                           space_before=6, space_after=0)
        elif t == "subtitle":
            _add_paragraph(doc, txt, _C, bold=True, first_line=False)
        elif t == "h1":
            _add_paragraph(doc, txt, _L, bold=True, first_line=False,
                           space_before=12, space_after=6)
        elif t == "quote":
            q = normalize_text(txt)
            if not q.startswith("«"):
                q = "«" + q.strip("«»") + "»"
            _add_paragraph(doc, q, _J, bold=False, first_line=True)
        elif t == "proshu":
            _add_paragraph(doc, txt, _J, bold=False, first_line=True, bold_word="ПРОШУ")
        elif t == "item":
            _add_paragraph(doc, txt, _J, bold=False, first_line=True)
        elif t == "table":
            _add_table(doc, b.get("rows") or [])
        elif t == "annex_h":
            _add_paragraph(doc, txt, _L, bold=True, first_line=False, space_before=12)
        elif t == "annex_item":
            _add_paragraph(doc, txt, _J, bold=False, first_line=True)
        elif t == "sign_left":
            _add_paragraph(doc, txt, _L, bold=False, first_line=False)
        elif t == "sign_right":
            _add_paragraph(doc, txt, _R, bold=False, first_line=False)
        elif t == "spacer":
            p = doc.add_paragraph()
            _base_format(p, _J, first_line=False)
            _set_run_font(p.add_run(""))
        else:  # body
            _add_paragraph(doc, txt, _J, bold=False, first_line=True)

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()
