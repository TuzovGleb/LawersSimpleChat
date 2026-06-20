"""SotaOCR recognizer — async REST OCR (https://sotaocr.com).

SotaOCR is a job-based API: submit a document (``POST /v1/extract`` -> 202 + job
id), poll the job until it completes, then fetch the result as markdown/text.
Markdown preserves tables (as md tables), formulas (LaTeX) and multi-column
layout, which is why it's the primary recognizer for legal documents.

The whole submit->poll->fetch cycle is wrapped in one LangSmith ``tool`` run so it
nests under the per-document ``document_extraction`` trace started in the
endpoint (same tree the LLM recognizer's ChatOpenAI calls attach to). No-op when
tracing is disabled.
"""
from __future__ import annotations

import asyncio
import logging

import httpx
from langsmith.run_helpers import trace as langsmith_trace

from app.rag_core.recognizers.base import (
    RecognitionResult,
    RecognizerError,
    RecognizerUnavailable,
)
from app.services.document_extraction import file_extension

logger = logging.getLogger(__name__)

# Formats SotaOCR accepts (from the API docs). Anything else is handed to the
# next recognizer in the chain instead of wasting an upload on a guaranteed 415.
# NB: explicit allowlists (not a broad "image/*" prefix) so gif/heic/heif — which
# SotaOCR does NOT accept — fall through to the LLM recognizer's vision path.
_SUPPORTED_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
_SUPPORTED_MIMES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/bmp",
    "image/tiff",
    "image/tif",
}

# Statuses (the API documents "pending"/"completed"; we treat anything matching
# these substrings as terminal failure, everything else as still in flight).
_FAILED_HINTS = ("fail", "error", "cancel", "reject")


def _supports(mime_type: str, ext: str) -> bool:
    return ext in _SUPPORTED_EXTS or mime_type.lower() in _SUPPORTED_MIMES


class SotaOcrClient:
    """Thin async client for the SotaOCR extract/poll/result endpoints.

    One :class:`httpx.AsyncClient` is opened per :meth:`extract` call (covering
    submit + every poll + the final fetch) and closed at the end. A custom
    ``transport`` can be injected for tests (``httpx.MockTransport``).
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        output_format: str = "markdown",
        model_profile: str = "fast",
        poll_interval_seconds: float = 1.0,
        job_timeout_seconds: float = 1800.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._output_format = output_format
        self._model_profile = model_profile
        self._poll_interval = max(poll_interval_seconds, 1.0)
        self._job_timeout = job_timeout_seconds
        self._transport = transport

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def extract(self, data: bytes, filename: str, mime_type: str) -> str:
        # Per-request timeouts: generous write for large uploads, modest read for
        # the (fast, async) submit/poll/fetch calls. The overall job is bounded by
        # the poll loop's deadline (job_timeout_seconds), not a single read timeout.
        timeout = httpx.Timeout(120.0, connect=30.0, write=300.0)
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=timeout, transport=self._transport
        ) as client:
            job_id, page_count = await self._submit(client, data, filename, mime_type)
            await self._await_completion(client, job_id, page_count)
            return await self._fetch_result(client, job_id)

    async def _submit(
        self, client: httpx.AsyncClient, data: bytes, filename: str, mime_type: str
    ) -> tuple[str, int]:
        files = {"file": (filename, data, mime_type or "application/octet-stream")}
        form: dict[str, str] = {}
        if self._model_profile:
            form["model_profile"] = self._model_profile

        resp = await client.post("/v1/extract", headers=self._headers, files=files, data=form)
        if resp.status_code == 415:
            raise RecognizerUnavailable(f"sotaocr: unsupported media ({mime_type})")
        if resp.status_code == 403:
            raise RecognizerError("sotaocr: insufficient balance")
        if resp.status_code == 401:
            raise RecognizerError("sotaocr: unauthorized (check SOTAOCR_API_KEY)")
        resp.raise_for_status()
        job = resp.json()
        job_id = job.get("id")
        if not job_id:
            raise RecognizerError(f"sotaocr: no job id in response {job!r}")
        return job_id, int(job.get("page_count") or 0)

    async def _await_completion(
        self, client: httpx.AsyncClient, job_id: str, page_count: int
    ) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._job_timeout
        while True:
            await asyncio.sleep(self._poll_interval)
            resp = await client.get(f"/v1/jobs/{job_id}", headers=self._headers)
            resp.raise_for_status()
            status = resp.json()
            state = str(status.get("status") or "").lower()
            done = int(status.get("pages_completed") or 0)
            total = int(status.get("page_count") or page_count or 0)

            if state == "completed" or (total > 0 and done >= total):
                return
            if any(hint in state for hint in _FAILED_HINTS):
                raise RecognizerError(f"sotaocr: job {job_id} failed (status={state})")
            if loop.time() >= deadline:
                raise RecognizerError(
                    f"sotaocr: job {job_id} timed out after {self._job_timeout:.0f}s "
                    f"(status={state}, {done}/{total} pages)"
                )

    async def _fetch_result(self, client: httpx.AsyncClient, job_id: str) -> str:
        resp = await client.get(
            f"/v1/jobs/{job_id}/result",
            headers=self._headers,
            params={"format": self._output_format},
        )
        # 202 here means the job slipped back to "not ready" between status and
        # result — surface it as a failure so the fallback chain takes over.
        if resp.status_code == 202:
            raise RecognizerError(f"sotaocr: result for job {job_id} not ready")
        resp.raise_for_status()
        try:
            payload = resp.json()
        except ValueError:
            # Non-JSON body (e.g. raw text/markdown returned for format=text|markdown).
            return resp.text
        if isinstance(payload, str):
            return payload
        # Documented shape: {"job_id", "format", "page_count", "content": "..."}.
        # Tolerate a couple of plausible alternatives rather than hard-failing.
        return (
            payload.get("content")
            or payload.get(self._output_format)
            or payload.get("text")
            or payload.get("markdown")
            or ""
        )


class SotaOcrRecognizer:
    """Wraps :class:`SotaOcrClient` behind the :class:`Recognizer` protocol."""

    def __init__(self, client: SotaOcrClient):
        self._client = client

    async def recognize(self, data: bytes, mime_type: str, filename: str) -> RecognitionResult:
        if not self._client.configured:
            raise RecognizerUnavailable("sotaocr: SOTAOCR_API_KEY not configured")

        ext = file_extension(filename)
        if not _supports(mime_type, ext):
            raise RecognizerUnavailable(f"sotaocr: unsupported format ({ext or mime_type})")

        try:
            with langsmith_trace(
                name="sotaocr_extract",
                run_type="tool",
                metadata={"filename": filename, "mime_type": mime_type},
            ):
                text = await self._client.extract(data, filename, mime_type)
        except (RecognizerError, RecognizerUnavailable):
            raise
        except httpx.HTTPError as err:
            raise RecognizerError(f"sotaocr: HTTP error: {err}") from err

        if not text.strip():
            raise RecognizerError("sotaocr: empty content")
        logger.info(
            "SotaOCR recognized document",
            extra={"doc_filename": filename, "raw_text_length": len(text)},
        )
        return RecognitionResult(text=text, strategy="sotaocr")
