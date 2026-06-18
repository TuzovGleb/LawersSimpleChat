"""Tests for the document extraction port (routing parity + native parsers)."""
import io
import shutil

import pytest

from app.services import document_extraction as de


# --- fake LLM extractor that records which path the dispatcher took ---

class FakeLlm:
    def __init__(self, file_attachment="LLM-FILE", vision="VISION", per_page="PER-PAGE"):
        self._file_attachment = file_attachment
        self._vision = vision
        self._per_page = per_page
        self.calls: list[str] = []

    async def vision(self, data, mime_type, filename):
        self.calls.append("vision")
        return self._vision

    async def file_attachment(self, data, filename):
        self.calls.append("file_attachment")
        return self._file_attachment

    async def pdf_per_page(self, data, filename):
        self.calls.append("pdf_per_page")
        return self._per_page


# --- detection helpers ---

def test_detection_helpers():
    assert de.is_plain_text("text/markdown", ".md")
    assert de.is_plain_text("application/octet-stream", ".csv")
    assert de.is_docx("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "")
    assert de.is_docx("application/octet-stream", ".docx")
    assert de.is_doc("application/msword", "")
    assert de.is_pdf("application/pdf", "")
    assert de.is_pdf("application/octet-stream", ".pdf")
    assert de.is_image("image/png", "")
    assert de.is_image("application/octet-stream", ".heic")
    assert not de.is_image("application/pdf", ".pdf")


def test_file_extension_and_mime():
    assert de.file_extension("Дело.PDF") == ".pdf"
    assert de.file_extension("noext") == ""
    assert de.mime_from_extension(".docx").endswith("wordprocessingml.document")
    assert de.mime_from_extension(".unknown") == "application/octet-stream"


def test_normalize_result_strips_nul_and_trims():
    r = de.normalize_result("  he\x00llo \n", "text")
    assert r.text == "hello"
    assert r.raw_text_length == len("hello")
    assert r.truncated is False
    assert r.strategy == "text"


# --- routing parity (native parsers monkeypatched for determinism) ---

@pytest.fixture
def patch_natives(monkeypatch):
    state = {"pdf": "", "docx": "", "doc": ""}

    async def fake_pdf(data):
        return state["pdf"]

    async def fake_docx(data):
        return state["docx"]

    async def fake_doc(data):
        return state["doc"]

    monkeypatch.setattr(de, "extract_pdf", fake_pdf)
    monkeypatch.setattr(de, "extract_docx", fake_docx)
    monkeypatch.setattr(de, "extract_doc", fake_doc)
    return state


async def test_route_plain_text_no_llm():
    llm = FakeLlm()
    r = await de.extract_text_from_document(b"hello world", "text/plain", "a.txt", llm)
    assert r.strategy == "text" and r.text == "hello world"
    assert llm.calls == []


async def test_route_docx_native_then_fallback(patch_natives):
    llm = FakeLlm()
    patch_natives["docx"] = "real docx body"
    r = await de.extract_text_from_document(b"x", "x", "a.docx", llm)
    assert r.strategy == "docx" and r.text == "real docx body"
    assert llm.calls == []

    patch_natives["docx"] = ""  # empty -> LLM file fallback
    r = await de.extract_text_from_document(b"x", "x", "a.docx", llm)
    assert r.strategy == "llm-file" and r.text == "LLM-FILE"
    assert llm.calls == ["file_attachment"]


async def test_route_doc_native_then_fallback(patch_natives):
    llm = FakeLlm()
    patch_natives["doc"] = "legacy doc body"
    r = await de.extract_text_from_document(b"x", "application/msword", "a.doc", llm)
    assert r.strategy == "doc"
    assert llm.calls == []

    patch_natives["doc"] = ""
    r = await de.extract_text_from_document(b"x", "application/msword", "a.doc", llm)
    assert r.strategy == "llm-file"


async def test_route_pdf_threshold(patch_natives):
    # >= 80 chars of native text -> "pdf"
    llm = FakeLlm()
    patch_natives["pdf"] = "A" * de.MIN_TEXT_LENGTH_FOR_SUCCESS
    r = await de.extract_text_from_document(b"x", "application/pdf", "a.pdf", llm)
    assert r.strategy == "pdf"
    assert llm.calls == []

    # < 80 chars -> per-page OCR
    llm = FakeLlm()
    patch_natives["pdf"] = "A" * (de.MIN_TEXT_LENGTH_FOR_SUCCESS - 1)
    r = await de.extract_text_from_document(b"x", "application/pdf", "a.pdf", llm)
    assert r.strategy == "pdf-pages" and r.text == "PER-PAGE"
    assert llm.calls == ["pdf_per_page"]

    # < 80 chars and per-page returns None -> single file-attachment
    llm = FakeLlm(per_page=None)
    patch_natives["pdf"] = ""
    r = await de.extract_text_from_document(b"x", "application/pdf", "a.pdf", llm)
    assert r.strategy == "llm-file"
    assert llm.calls == ["pdf_per_page", "file_attachment"]


async def test_route_image_and_unknown(patch_natives):
    llm = FakeLlm()
    r = await de.extract_text_from_document(b"x", "image/png", "a.png", llm)
    assert r.strategy == "vision" and llm.calls == ["vision"]

    llm = FakeLlm()
    r = await de.extract_text_from_document(b"x", "application/zip", "a.zip", llm)
    assert r.strategy == "llm-file" and llm.calls == ["file_attachment"]


# --- native parsers on real files ---

def _build_pdf(text: str) -> bytes:
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    ]
    stream = b"BT /F1 18 Tf 72 700 Td (" + text.encode("latin-1") + b") Tj ET"
    objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref = len(out)
    n = len(objs) + 1
    out += b"xref\n0 " + str(n).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode()
    out += (b"trailer\n<< /Size " + str(n).encode() + b" /Root 1 0 R >>\nstartxref\n"
            + str(xref).encode() + b"\n%%EOF\n")
    return bytes(out)


def test_extract_pdf_sync_real():
    text = "DOCEXTRACT the quick brown fox jumps over the lazy dog again and again"
    extracted = de._extract_pdf_sync(_build_pdf(text))
    assert "DOCEXTRACT" in extracted


def test_extract_docx_sync_real():
    from docx import Document

    doc = Document()
    doc.add_paragraph("First paragraph DOCXMARKER")
    doc.add_paragraph("Second paragraph here")
    buf = io.BytesIO()
    doc.save(buf)
    extracted = de._extract_docx_sync(buf.getvalue())
    assert "DOCXMARKER" in extracted
    assert "Second paragraph" in extracted


def test_extract_pdf_sync_garbage_returns_empty():
    assert de._extract_pdf_sync(b"not a pdf at all") == ""


@pytest.mark.skipif(shutil.which("antiword") is None, reason="antiword not installed")
async def test_extract_doc_missing_antiword_or_runs():
    # Smoke: with garbage bytes antiword should fail cleanly -> "" (no raise).
    assert await de.extract_doc(b"garbage") == ""
