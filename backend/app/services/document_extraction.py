"""Document text extraction — faithful Python port of lib/document-processing.ts.

Routing, thresholds and fallback edges mirror the Next.js implementation 1:1 so
extracted text stays consistent after the migration. Native parsers (pdf/docx/doc)
are CPU-bound or spawn a subprocess, so they run OFF the FastAPI event loop.

The LLM/vision paths (vision, file-attachment, per-page scanned-PDF OCR) are
abstracted behind ``LlmExtractor`` and implemented separately (see the LLM
extractor module) so they can be traced through LangChain/LangSmith.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import tempfile
from dataclasses import dataclass
from typing import Literal, Protocol

logger = logging.getLogger(__name__)

# Mirror lib/document-processing.ts constants.
MIN_TEXT_LENGTH_FOR_SUCCESS = 80
PDF_PAGE_CONCURRENCY = 5
PDF_PAGE_MAX_RETRIES = 2
DOCUMENT_EXTRACTION_MAX_TOKENS = 16384

Strategy = Literal["text", "pdf", "docx", "doc", "vision", "llm-file", "pdf-pages"]

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic"}
_MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".txt": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


@dataclass
class ExtractedDocument:
    text: str
    raw_text_length: int
    truncated: bool
    strategy: Strategy


# --- mime / extension detection (mirror document-processing.ts) ---

def file_extension(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot >= 0 else ""


def is_plain_text(mime_type: str, ext: str) -> bool:
    return mime_type.startswith("text/") or ext in {".txt", ".md", ".csv", ".json"}


def is_docx(mime_type: str, ext: str) -> bool:
    return (
        mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        or ext == ".docx"
    )


def is_doc(mime_type: str, ext: str) -> bool:
    return mime_type == "application/msword" or ext == ".doc"


def is_pdf(mime_type: str, ext: str) -> bool:
    return mime_type == "application/pdf" or ext == ".pdf"


def is_image(mime_type: str, ext: str) -> bool:
    return mime_type.startswith("image/") or ext in _IMAGE_EXTS


def is_image_ext(ext: str) -> bool:
    return ext.lower() in _IMAGE_EXTS


def mime_from_extension(ext: str) -> str:
    return _MIME_BY_EXT.get(ext.lower(), "application/octet-stream")


def normalize_result(raw_text: str, strategy: Strategy) -> ExtractedDocument:
    cleaned = raw_text.replace("\x00", "").strip()
    return ExtractedDocument(
        text=cleaned, raw_text_length=len(cleaned), truncated=False, strategy=strategy
    )


# --- native parsers (CPU-bound / subprocess -> run off the event loop) ---

def _extract_pdf_sync(data: bytes) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        parts = [(page.extract_text() or "") for page in reader.pages]
        return "\n".join(parts).strip()
    except Exception:
        logger.warning("PDF native parse failed; will fall back", exc_info=True)
        return ""


def _extract_docx_sync(data: bytes) -> str:
    try:
        from docx import Document

        doc = Document(io.BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs).strip()
    except Exception:
        logger.warning("DOCX native parse failed; will fall back", exc_info=True)
        return ""


async def extract_pdf(data: bytes) -> str:
    return await asyncio.to_thread(_extract_pdf_sync, data)


async def extract_docx(data: bytes) -> str:
    return await asyncio.to_thread(_extract_docx_sync, data)


async def extract_doc(data: bytes) -> str:
    """Legacy .doc via antiword in a SEPARATE process (never blocks the loop)."""
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".doc", delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        proc = await asyncio.create_subprocess_exec(
            "antiword", tmp_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(
                "antiword failed rc=%s: %s",
                proc.returncode, err.decode("utf-8", "replace")[:300],
            )
            return ""
        return out.decode("utf-8", "replace").strip()
    except FileNotFoundError:
        logger.warning("antiword not installed; .doc native parse unavailable")
        return ""
    except Exception:
        logger.warning("DOC native parse failed; will fall back", exc_info=True)
        return ""
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# --- LLM extraction interface (concrete impl lives in the LLM extractor module) ---

class LlmExtractor(Protocol):
    async def vision(self, data: bytes, mime_type: str, filename: str) -> str: ...

    async def file_attachment(self, data: bytes, filename: str) -> str: ...

    async def pdf_per_page(self, data: bytes, filename: str) -> str | None: ...


# --- dispatcher (mirror extractTextFromDocument) ---

async def extract_text_from_document(
    data: bytes, mime_type: str, filename: str, llm: LlmExtractor
) -> ExtractedDocument:
    ext = file_extension(filename)

    if is_plain_text(mime_type, ext):
        # Match Node's buffer.toString('utf-8') (lossy replacement on bad bytes).
        return normalize_result(data.decode("utf-8", "replace"), "text")

    if is_docx(mime_type, ext):
        docx_text = await extract_docx(data)
        if docx_text:
            return normalize_result(docx_text, "docx")
        return normalize_result(await llm.file_attachment(data, filename), "llm-file")

    if is_doc(mime_type, ext):
        doc_text = await extract_doc(data)
        if doc_text:
            return normalize_result(doc_text, "doc")
        return normalize_result(await llm.file_attachment(data, filename), "llm-file")

    if is_pdf(mime_type, ext):
        pdf_text = await extract_pdf(data)
        if pdf_text and len(pdf_text) >= MIN_TEXT_LENGTH_FOR_SUCCESS:
            return normalize_result(pdf_text, "pdf")
        # Scanned PDF (no text layer): split into pages and OCR them in parallel.
        # Returns None -> fall back to a single file-attachment request.
        per_page = await llm.pdf_per_page(data, filename)
        if per_page:
            return normalize_result(per_page, "pdf-pages")
        return normalize_result(await llm.file_attachment(data, filename), "llm-file")

    if is_image(mime_type, ext):
        return normalize_result(await llm.vision(data, mime_type, filename), "vision")

    return normalize_result(await llm.file_attachment(data, filename), "llm-file")
