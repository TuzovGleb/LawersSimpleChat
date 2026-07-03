"""Tokenizer + reassembler for the drafting tool (variant C).

The classifier itself is an LLM call (not exercised here); we test the
deterministic pieces and the end-to-end shape into the renderer.
"""
import zipfile
import io
import re

from app.rag_core.llm import ChatProviderParams
from app.services.docx_drafting import (
    ClassifiedDoc,
    LineType,
    assemble_blocks,
    build_segmenter_llm,
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


def test_tokenize_strips_markdown_remnants():
    text = (
        "## I. РАЗДЕЛ\n"
        "Абзац с **жирным** и `кодом`.\n"
        "---\n"
        "____________\n"
        "> Цитата суда.\n"
    )
    units = tokenize_draft(text)
    texts = [u["text"] for u in units if u["kind"] == "content"]
    assert texts == [
        "I. РАЗДЕЛ",
        "Абзац с жирным и кодом.",
        "____________",  # строка-прочерк — плейсхолдер, НЕ markdown-линейка
        "Цитата суда.",
    ]
    # '---' стал спейсером
    assert [u["kind"] for u in units] == ["content", "content", "spacer", "content", "content"]


def test_strip_markdown_keeps_legitimate_text():
    from app.services.docx_drafting import _strip_markdown

    # Непарные ** между цифрами (степень) не трогаем.
    assert _strip_markdown("Сумма 2**10 рублей") == "Сумма 2**10 рублей"
    # Ведущий знак сравнения перед числом — не markdown-цитата.
    assert _strip_markdown("> 50 % голосов принадлежит истцу") == "> 50 % голосов принадлежит истцу"
    # А настоящая markdown-цитата перед словом снимается.
    assert _strip_markdown("> Цитата суда.") == "Цитата суда."
    # Парный жирный снимается.
    assert _strip_markdown("Абзац с **жирным** словом") == "Абзац с жирным словом"


def test_linetype_coerces_unknown_type_to_body():
    assert LineType(id=1, type="date").type == "body"
    assert LineType(id=2, type="header").type == "header"
    doc = ClassifiedDoc(
        file_name="Возражения",
        lines=[{"id": 1, "type": "title"}, {"id": 2, "type": "неизвестный"}],
    )
    assert [l.type for l in doc.lines] == ["title", "body"]


def _provider_params(models: dict) -> ChatProviderParams:
    return ChatProviderParams.model_validate(
        {
            "provider": {"api_key": "dummy"},
            "default_model": "fast",
            "models": models,
        }
    )


def test_segmenter_prefers_lite_model():
    params = _provider_params(
        {
            "fast": {"name": "vendor/big-model"},
            "lite": {"name": "vendor/small-model"},
        }
    )
    assert build_segmenter_llm(params).model_name == "vendor/small-model"


def test_segmenter_falls_back_to_default_without_lite():
    params = _provider_params({"fast": {"name": "vendor/big-model"}})
    assert build_segmenter_llm(params).model_name == "vendor/big-model"


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
