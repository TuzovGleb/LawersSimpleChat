"""LLM/vision recognizer via LangChain ChatOpenAI (OpenRouter).

The resilience fallback behind SotaOCR: when SotaOCR is unavailable (no key,
out of balance, upstream down) or rejects a format, this recognizer extracts
text with a vision-capable OpenRouter model. Every call is a ChatOpenAI
invocation, so it auto-traces in LangSmith under the per-document parent run.

PDFs go through the per-page text-layer gate first (``pdf_gate.py``): pages
whose text layer explains the rendered ink are taken from the layer (zero LLM
calls for born-digital documents); only true scan/garbled pages are OCR'd —
under a per-page hard wall, an absolute extraction deadline and a circuit
breaker, assembling a PARTIAL result instead of discarding good pages when
something stalls. Per-page OCR sends each page as an OpenRouter ``type:'file'``
content part — verified to pass through langchain-openai 1.2.2 intact (see
backend/experiments/spike_typefile/spike.py).
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import random
import threading
import time
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage
from openai import APIStatusError

from app.rag_core.recognizers.base import RecognitionResult, RecognizerError
from app.rag_core.recognizers.pdf_gate import evaluate_pdf
from app.services.document_extraction import (
    PDF_CIRCUIT_BREAKER_THRESHOLD,
    PDF_DEADLINE_RESERVE,
    PDF_EXTRACTION_HARD_BUDGET,
    PDF_PAGE_CONCURRENCY,
    PDF_PAGE_HARD_TIMEOUT,
    PDF_PAGE_MAX_RETRIES,
    extraction_deadline,
    file_extension,
    is_image,
    is_image_ext,
    is_pdf,
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

# macOS AppleDouble sidecar ("._<name>"): xattr metadata, not a document. Rides
# along when mail archives are unpacked and inherits the real file's extension.
_APPLEDOUBLE_MAGIC = b"\x00\x05\x16\x07"


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


class _PdfPageSource:
    """Lazily materializes single-page PDFs from the original bytes.

    Replaces the old eager split that held ALL page blobs in memory for the
    whole run (2x+ resident memory on a 1500-page doc — an OOM before any
    timeout). One shared pypdf reader guarded by a lock (pypdf is not
    thread-safe); the ~ms CPU per page is negligible next to the OCR call.
    """

    def __init__(self, data: bytes):
        self._data = data
        self._lock = threading.Lock()
        self._reader = None

    def probe(self) -> None:
        """Open the shared reader now — pdfium and pypdf disagree on some PDFs,
        so an unreadable-for-pypdf doc must fail ONCE up front (routing to the
        whole-file fallback), not once per page through the breaker."""
        from pypdf import PdfReader

        with self._lock:
            if self._reader is None:
                self._reader = PdfReader(io.BytesIO(self._data))

    def page_bytes(self, index: int) -> bytes:
        from pypdf import PdfReader, PdfWriter

        with self._lock:
            if self._reader is None:
                self._reader = PdfReader(io.BytesIO(self._data))
            writer = PdfWriter()
            writer.add_page(self._reader.pages[index])
            buf = io.BytesIO()
            writer.write(buf)
            return buf.getvalue()


@dataclass(frozen=True)
class PdfPerPageResult:
    """Assembly of a gated per-page extraction (partial results allowed)."""

    text: str
    pages_total: int
    pages_from_layer: int
    pages_ocred: int
    pages_failed: int


# --- rate-limit (429) back-off for per-page OCR ---
# ChatOpenAI already retries 429/5xx internally (max_retries=2, honouring
# Retry-After); this adds an extra outer back-off so a burst of parallel page
# requests (up to PDF_PAGE_CONCURRENCY) that all get throttled don't immediately
# re-fire. Non-429 errors retry immediately, as before.
_RATE_LIMIT_BASE_DELAY = 2.0   # seconds (exponential base when no Retry-After)
_RATE_LIMIT_MAX_DELAY = 30.0


def _is_rate_limit(err: Exception) -> bool:
    if err.__class__.__name__ == "RateLimitError":
        return True
    if getattr(err, "status_code", None) == 429:
        return True
    msg = str(err).lower()
    return "429" in msg or "too many requests" in msg or "rate limit" in msg


def _retry_after_seconds(err: Exception) -> float | None:
    headers = getattr(getattr(err, "response", None), "headers", None)
    if not headers:
        return None
    val = headers.get("retry-after") or headers.get("Retry-After")
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _rate_limit_delay(err: Exception, attempt: int) -> float:
    """Retry-After if the server sent one, else exponential back-off with full jitter."""
    retry_after = _retry_after_seconds(err)
    if retry_after is not None:
        return min(retry_after, _RATE_LIMIT_MAX_DELAY)
    cap = min(_RATE_LIMIT_BASE_DELAY * (2 ** attempt), _RATE_LIMIT_MAX_DELAY)
    return random.uniform(0, cap)


class LlmRecognizer:
    """Vision / file-attachment / per-page OCR via a pre-built ChatOpenAI."""

    def __init__(self, llm):
        # A LangChain ChatOpenAI pre-built for the OCR model (e.g. gemini-3.5-flash).
        self._llm = llm

    async def recognize(self, data: bytes, mime_type: str, filename: str) -> RecognitionResult:
        """Dispatch by format: images -> vision; PDFs -> gated per-page path
        (text layer where it explains the ink, OCR elsewhere); everything else
        -> file-attachment."""
        ext = file_extension(filename)
        if is_image(mime_type, ext):
            return RecognitionResult(await self.vision(data, mime_type, filename), "vision")
        if is_pdf(mime_type, ext):
            per = await self.pdf_per_page(data, filename)
            if per is None:
                # The whole-file fallback exists for real-but-broken PDFs
                # (encrypted, damaged xref) — those still carry a %PDF- header
                # STARTING within the first 1024 bytes (pdfium accepts offsets
                # 0..1024 inclusive, hence the +5 so a boundary header isn't
                # cut mid-token). Bytes without one aren't a PDF at all (e.g.
                # macOS "._*" AppleDouble sidecars uploaded alongside the real
                # file) and would only make the provider 400.
                if b"%PDF-" not in data[: 1024 + 5]:
                    raise RecognizerError(
                        "macOS AppleDouble sidecar uploaded as a PDF"
                        if data[:4] == _APPLEDOUBLE_MAGIC
                        else "claimed PDF has no %PDF- header"
                    )
                return RecognitionResult(await self.file_attachment(data, filename), "llm-file")
            recognized = per.pages_from_layer + per.pages_ocred
            if recognized == 0:
                if per.pages_total <= 3:
                    # A tiny doc may still fit one whole-file request.
                    return RecognitionResult(await self.file_attachment(data, filename), "llm-file")
                # A whole-PDF single request cannot honestly transcribe a large
                # document (max_tokens) — surface the failure instead.
                raise RecognizerError(
                    f"per-page extraction produced 0/{per.pages_total} pages"
                )
            strategy = (
                "pdf-text-layer"
                if per.pages_ocred == 0 and per.pages_failed == 0
                else "pdf-pages"
            )
            return RecognitionResult(
                per.text,
                strategy,
                truncated=per.pages_failed > 0,
                pages_total=per.pages_total,
                pages_recognized=recognized,
            )
        return RecognitionResult(await self.file_attachment(data, filename), "llm-file")

    async def _run(self, content: list, system: str, *, run_name=None, metadata=None) -> str:
        # run_name/metadata land on the LangSmith child run (nested under the
        # document_extraction parent trace started in the endpoint).
        config: dict = {}
        if run_name:
            config["run_name"] = run_name
        if metadata:
            config["metadata"] = metadata
        try:
            message = await self._llm.ainvoke(
                [SystemMessage(content=system), HumanMessage(content=content)],
                config=config or None,
            )
        except APIStatusError as err:
            # 400/413/415/422 = the provider rejected the INPUT (unreadable
            # file, oversized or unsupported content) — a recognition failure,
            # not an internal error: map it to RecognizerError so the endpoint
            # answers 422 instead of 500. Everything else (401/403 config
            # errors, 429, 5xx outages) is not a property of the document and
            # propagates unchanged (429 keeps its retry/back-off semantics).
            if err.status_code in (400, 413, 415, 422):
                raise RecognizerError(f"provider rejected the document: {err}") from err
            raise
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
        for attempt in range(PDF_PAGE_MAX_RETRIES + 1):
            try:
                return await self._run(
                    content, _EXTRACTION_SYSTEM,
                    run_name="document_ocr_page",
                    metadata={"page_index": page_no, "total_pages": total},
                )
            except Exception as err:  # noqa: BLE001 - retried below
                last_error = err
                if attempt >= PDF_PAGE_MAX_RETRIES:
                    break
                if _is_rate_limit(err):
                    delay = _rate_limit_delay(err, attempt)
                    logger.warning(
                        "OCR page rate-limited (429); backing off",
                        extra={"page": page_no, "attempt": attempt + 1, "delay_s": round(delay, 2)},
                    )
                    await asyncio.sleep(delay)
                # non-429 errors retry immediately (unchanged behaviour)
        raise last_error or RuntimeError(f"page {page_no} extraction failed")

    async def pdf_per_page(self, data: bytes, filename: str) -> PdfPerPageResult | None:
        """Gated per-page extraction. Never all-or-nothing: pages the text layer
        explains are taken from the layer; the rest are OCR'd under a per-page
        hard wall, an absolute extraction deadline and a circuit breaker. Pages
        that still fail are dropped from the text (counted in ``pages_failed``),
        not padded with placeholders. Returns None for unopenable PDFs."""
        # Bound the gate by the same absolute deadline as the OCR phase (converted
        # to the monotonic domain the worker thread can check cooperatively).
        gate_stop = None
        outer_deadline = extraction_deadline()
        if outer_deadline is not None:
            loop0 = asyncio.get_running_loop()
            gate_stop = time.monotonic() + max(
                0.0, outer_deadline - PDF_DEADLINE_RESERVE - loop0.time()
            )
        gate = await asyncio.to_thread(evaluate_pdf, data, gate_stop)
        if gate is None:
            return None

        total = len(gate.pages)
        parts: list[str | None] = [
            None if p.should_ocr else p.text_from_layer for p in gate.pages
        ]
        ocr_indices = gate.ocr_indices
        pages_from_layer = total - len(ocr_indices)
        if not ocr_indices:
            text = "\n\n".join(p for p in parts if p and p.strip()).strip()
            return PdfPerPageResult(text, total, pages_from_layer, 0, 0)

        loop = asyncio.get_running_loop()
        # Absolute deadline set at endpoint entry (counts S3 download + gate);
        # fall back to a local budget when called outside the endpoint (tests).
        deadline = extraction_deadline() or (loop.time() + PDF_EXTRACTION_HARD_BUDGET)
        stop_at = deadline - PDF_DEADLINE_RESERVE

        source = _PdfPageSource(data)
        try:
            await asyncio.to_thread(source.probe)
        except Exception:  # noqa: BLE001 - pdfium-openable but pypdf-unopenable
            logger.warning(
                "pypdf cannot open document; per-page OCR unavailable",
                extra={"doc_filename": filename, "pages": total}, exc_info=True,
            )
            if pages_from_layer == 0:
                return None  # recognize() falls back to the whole-file request
            text = "\n\n".join(p for p in parts if p and p.strip()).strip()
            return PdfPerPageResult(text, total, pages_from_layer, 0, len(ocr_indices))

        sem = asyncio.Semaphore(PDF_PAGE_CONCURRENCY)
        results: dict[int, str] = {}
        # Circuit breaker: a run of page failures with zero interleaved successes
        # means new requests only feed a systemic outage — stop scheduling, keep
        # what we have. Consecutive count is class-AGNOSTIC: the per-page wall
        # itself manufactures class heterogeneity during a degraded outage
        # (stalls become TimeoutError while fast refusals keep their own class),
        # so a same-class-only run never accumulates under concurrency.
        breaker = {"cls": None, "run": 0, "tripped": False}

        def _record_failure(err: BaseException) -> None:
            breaker["run"] += 1
            # last class seen, for the trip log only
            breaker["cls"] = "rate-limit" if _is_rate_limit(err) else type(err).__name__
            if breaker["run"] >= PDF_CIRCUIT_BREAKER_THRESHOLD and not breaker["tripped"]:
                breaker["tripped"] = True
                logger.warning(
                    "per-page OCR circuit breaker tripped; saving partial",
                    extra={"doc_filename": filename, "error_class": breaker["cls"],
                           "consecutive": breaker["run"], "pages": total},
                )

        async def do_page(index: int) -> None:
            async with sem:
                if breaker["tripped"] or loop.time() >= stop_at:
                    return
                try:
                    # Hard wall around the WHOLE page attempt (retries included):
                    # without it, retries over the 300s ChatOpenAI timeout
                    # compound to ~45 min per page during a provider stall.
                    async with asyncio.timeout(PDF_PAGE_HARD_TIMEOUT):
                        page_bytes = await asyncio.to_thread(source.page_bytes, index)
                        text = await self._extract_single_page(
                            page_bytes, filename, index + 1, total
                        )
                except Exception as err:  # noqa: BLE001 - partial result, not fatal
                    _record_failure(err)
                    logger.warning(
                        "page OCR failed; continuing without it",
                        extra={"doc_filename": filename, "page": index + 1},
                        exc_info=True,
                    )
                    return
                breaker["cls"], breaker["run"] = None, 0
                if not text:
                    # The request completed (breaker reset above) but transcribed
                    # nothing from an ink-bearing page: count the page as failed
                    # so the document stays truncated/re-extractable instead of
                    # silently losing the page forever.
                    logger.warning(
                        "page OCR returned empty text for an ink-bearing page; counting as failed",
                        extra={"doc_filename": filename, "page": index + 1},
                    )
                    return
                results[index] = text

        tasks = [asyncio.create_task(do_page(i)) for i in ocr_indices]
        try:
            async with asyncio.timeout_at(stop_at):
                await asyncio.gather(*tasks, return_exceptions=True)
        except TimeoutError:
            logger.warning(
                "extraction deadline reached; saving partial",
                extra={"doc_filename": filename, "pages": total,
                       "pages_done": pages_from_layer + len(results)},
            )
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        for index, text in results.items():
            parts[index] = text
        failed = len(ocr_indices) - len(results)
        text = "\n\n".join(p for p in parts if p and p.strip()).strip()
        return PdfPerPageResult(text, total, pages_from_layer, len(results), failed)
