"""LLM/vision document extraction via LangChain ChatOpenAI (OpenRouter).

Concrete ``LlmExtractor`` for app/services/document_extraction.py. Every call is
a ChatOpenAI invocation so it auto-traces in LangSmith (no decorators). Faithful
port of the vision / file-attachment / per-page paths from
lib/document-processing.ts.

Per-page scanned-PDF OCR sends each page as an OpenRouter ``type:'file'`` content
part — verified to pass through langchain-openai 1.2.2 intact
(see backend/experiments/spike_typefile/spike.py).
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from app.services.document_extraction import (
    PDF_PAGE_CONCURRENCY,
    PDF_PAGE_MAX_RETRIES,
    file_extension,
    is_image_ext,
    mime_from_extension,
)

logger = logging.getLogger(__name__)

_VISION_SYSTEM = (
    "You are a precise transcription assistant. Extract all legible text from "
    "provided legal document images. Preserve the original wording and paragraph "
    "structure when possible."
)
_EXTRACTION_SYSTEM = (
    "You are a meticulous legal transcription assistant. Extract the complete "
    "plain text from provided documents without adding commentary."
)


def _extract_content(message) -> str:
    """Mirror extractContentFromCompletion: AIMessage content may be str or list."""
    content = getattr(message, "content", message)
    if not content:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and "text" in item:
                parts.append(str(item["text"]))
        return "\n".join(parts).strip()
    return ""


def _split_pdf_pages_sync(data: bytes) -> list[bytes] | None:
    """Split a PDF into single-page PDFs. None => not splittable / <=1 page."""
    try:
        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(io.BytesIO(data))
        count = len(reader.pages)
        if count <= 1:
            return None
        pages: list[bytes] = []
        for i in range(count):
            writer = PdfWriter()
            writer.add_page(reader.pages[i])
            buf = io.BytesIO()
            writer.write(buf)
            pages.append(buf.getvalue())
        return pages
    except Exception:
        logger.warning("PDF split failed; fall back to single request", exc_info=True)
        return None


class LlmDocumentExtractor:
    def __init__(self, llm):
        # A LangChain ChatOpenAI pre-built for the extraction model (gemini-3.5-flash).
        self._llm = llm

    async def _run(self, content: list, system: str, *, run_name=None, metadata=None) -> str:
        # run_name/metadata land on the LangSmith child run (nested under the
        # document_extraction parent trace started in the endpoint).
        config: dict = {}
        if run_name:
            config["run_name"] = run_name
        if metadata:
            config["metadata"] = metadata
        message = await self._llm.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=content)],
            config=config or None,
        )
        return _extract_content(message)

    async def vision(self, data: bytes, mime_type: str, filename: str) -> str:
        b64 = base64.b64encode(data).decode()
        content = [
            {
                "type": "text",
                "text": f"Transcribe the text content from this document image: {filename}. Return only the text.",
            },
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
        ]
        return await self._run(content, _VISION_SYSTEM, run_name="document_vision")

    async def file_attachment(self, data: bytes, filename: str) -> str:
        ext = file_extension(filename)
        b64 = base64.b64encode(data).decode()
        mime = mime_from_extension(ext)
        content: list = [
            {
                "type": "text",
                "text": f'Read the attached document "{filename}" and return only its textual content.',
            }
        ]
        if ext == ".pdf" or is_image_ext(ext):
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        else:
            try:
                content.append({"type": "text", "text": f"Document content:\n\n{data.decode('utf-8')}"})
            except UnicodeDecodeError:
                content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        return await self._run(content, _EXTRACTION_SYSTEM, run_name="document_file_attachment")

    async def _extract_single_page(
        self, page_bytes: bytes, filename: str, page_no: int, total: int
    ) -> str:
        b64 = base64.b64encode(page_bytes).decode()
        content = [
            {
                "type": "text",
                "text": (
                    f'This is page {page_no} of {total} of "{filename}". Return ONLY its '
                    "full textual content verbatim, preserving paragraph structure. No commentary."
                ),
            },
            {
                "type": "file",
                "file": {
                    "filename": f"{filename}#page-{page_no}",
                    "file_data": f"data:application/pdf;base64,{b64}",
                },
            },
        ]
        last_error: Exception | None = None
        for _attempt in range(PDF_PAGE_MAX_RETRIES + 1):
            try:
                return await self._run(
                    content, _EXTRACTION_SYSTEM,
                    run_name="document_ocr_page",
                    metadata={"page_index": page_no, "total_pages": total},
                )
            except Exception as err:  # noqa: BLE001 - retried below
                last_error = err
        raise last_error or RuntimeError(f"page {page_no} extraction failed")

    async def pdf_per_page(self, data: bytes, filename: str) -> str | None:
        pages = await asyncio.to_thread(_split_pdf_pages_sync, data)
        if not pages:
            return None

        total = len(pages)
        sem = asyncio.Semaphore(PDF_PAGE_CONCURRENCY)

        async def do_page(index: int, page_bytes: bytes) -> str:
            async with sem:
                return await self._extract_single_page(page_bytes, filename, index + 1, total)

        try:
            # all-or-nothing: any page failing (after retries) -> None so the
            # caller falls back to a single request rather than saving a doc with holes.
            results = await asyncio.gather(*(do_page(i, p) for i, p in enumerate(pages)))
        except Exception:
            logger.warning(
                "per-page extraction failed; fall back to single request",
                # NB: 'filename' is a reserved LogRecord attr — use doc_filename.
                extra={"doc_filename": filename, "pages": total}, exc_info=True,
            )
            return None
        return "\n\n".join(results).strip()
