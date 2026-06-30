"""Tokenizer + reassembler for the drafting tool (variant C).

The classifier itself is an LLM call (not exercised here); we test the
deterministic pieces and the end-to-end shape into the renderer.
"""
import zipfile
import io
import re

from app.services.docx_drafting import (
    ClassifiedDoc,
    assemble_blocks,
    classify_lines,
    tokenize_draft,
)
from app.services.docx_builder import render_docx


def _kinds(units):
    return [u["kind"] for u in units]


def test_tokenize_lines_and_ids():
    text = "В суд\nИстец: Иванов\n\nВОЗРАЖЕНИЯ\n\nТекст абзаца."
    units = tokenize_draft(text)
    assert _kinds(units) == ["content", "content", "spacer", "content", "spacer", "content"]
    ids = [u["id"] for u in units if u["kind"] == "content"]
    assert ids == [1, 2, 3, 4]  # sequential, only over content lines


def test_tokenize_collapses_and_trims_spacers():
    text = "\n\n\nЗаголовок\n\n\n\nТело\n\n\n"
    units = tokenize_draft(text)
    # leading/trailing blanks trimmed; the inner run collapses to ONE spacer
    assert _kinds(units) == ["content", "spacer", "content"]


def test_tokenize_table_run_and_separator():
    text = (
        "Расчёт:\n"
        "Период | Дней | Сумма\n"
        "|---|---|---|\n"
        "2024 | 254 | 125 000\n"
        "Итого | | 412 300\n"
        "Вывод."
    )
    units = tokenize_draft(text)
    assert _kinds(units) == ["content", "table", "content"]
    table = units[1]
    # separator row dropped; 3 real rows, cells parsed
    assert table["rows"] == [
        ["Период", "Дней", "Сумма"],
        ["2024", "254", "125 000"],
        ["Итого", "", "412 300"],
    ]


def test_tokenize_pipe_guard_needs_two_cells():
    # a stray single '|' in prose is NOT a table row
    units = tokenize_draft("Истец | ответчик в одном абзаце без таблицы только начало|")
    assert _kinds(units) == ["content"]


def test_assemble_drops_spacer_between_flowing_paragraphs():
    units = [
        {"kind": "content", "id": 1, "text": "Абзац один."},
        {"kind": "spacer"},  # between two bodies -> dropped
        {"kind": "content", "id": 2, "text": "Абзац два."},
        {"kind": "spacer"},  # before an h1 -> kept
        {"kind": "content", "id": 3, "text": "II. РАЗДЕЛ"},
    ]
    blocks = assemble_blocks(units, {1: "body", 2: "body", 3: "h1"})
    assert [b["type"] for b in blocks] == ["body", "body", "spacer", "h1"]


def test_assemble_blocks_interleaves_and_falls_back_to_body():
    units = [
        {"kind": "content", "id": 1, "text": "ВОЗРАЖЕНИЯ"},
        {"kind": "spacer"},
        {"kind": "content", "id": 2, "text": "Текст без классификации"},
        {"kind": "table", "rows": [["A", "B"], ["1", "2"]]},
    ]
    types = {1: "title"}  # id 2 missing -> body
    blocks = assemble_blocks(units, types)
    assert [b["type"] for b in blocks] == ["title", "spacer", "body", "table"]
    assert blocks[3]["rows"] == [{"cells": ["A", "B"]}, {"cells": ["1", "2"]}]


def test_end_to_end_assemble_renders_valid_docx():
    text = (
        "В кассационный суд\n"
        "Истец: ____________\n"
        "\n"
        "ПИСЬМЕННЫЕ ВОЗРАЖЕНИЯ\n"
        "\n"
        "I. ВВОДНАЯ ЧАСТЬ\n"
        "В производстве суда находилось дело по ст. 379.7 ГК РФ.\n"
        "\n"
        "На основании изложенного ПРОШУ: отказать."
    )
    units = tokenize_draft(text)
    content = [u for u in units if u["kind"] == "content"]
    # mock classifier output
    types = {1: "header", 2: "header", 3: "title", 4: "h1", 5: "body", 6: "proshu"}
    assert len(content) == 6
    blocks = assemble_blocks(units, types)
    data = render_docx(blocks)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    assert "Times New Roman" in xml
    assert len(re.findall(r"<w:p[ >]", xml)) == len(blocks)  # one paragraph per block
    assert xml.count('w:val="center"') >= 1  # the title is centered


def test_classify_schema_binds():
    # No network: with_structured_output just binds the schema as a tool.
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(model="anthropic/claude-sonnet-latest", api_key="dummy")
    runnable = llm.with_structured_output(ClassifiedDoc)
    assert runnable is not None
    schema = ClassifiedDoc.model_json_schema()
    assert set(schema["properties"]) == {"file_name", "lines"}
    # 12 content types (no spacer/table)
    assert len(schema["$defs"]["LineType"]["properties"]["type"]["enum"]) == 12
