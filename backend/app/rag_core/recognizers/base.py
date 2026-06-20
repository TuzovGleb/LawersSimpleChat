"""Recognizer abstraction: the contract every OCR/text recognizer implements.

Deliberately import-free (no httpx / langchain / services) so it can be imported
from anywhere — including ``services/document_extraction.py`` for type hints —
without creating an import cycle with the concrete recognizers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class RecognitionResult:
    """Text produced by a recognizer plus the strategy label that produced it.

    ``strategy`` is persisted to ``project_documents.strategy`` and logged, so it
    must stay stable: ``sotaocr`` for SotaOCR; ``vision`` / ``pdf-pages`` /
    ``llm-file`` for the LLM recognizer (unchanged from the pre-migration values).
    """

    text: str
    strategy: str


@runtime_checkable
class Recognizer(Protocol):
    """Turn raw document bytes into text. Format dispatch is the recognizer's own
    concern (a PDF-only engine raises :class:`RecognizerUnavailable` for images,
    etc.), so callers hand every OCR-able document to the same interface."""

    async def recognize(self, data: bytes, mime_type: str, filename: str) -> RecognitionResult: ...


class RecognizerError(Exception):
    """A recognizer failed to produce text (HTTP error, timeout, empty result).

    Recoverable by the fallback chain: it logs and tries the next recognizer.
    When every recognizer raises, the chain re-raises so the endpoint can map it
    to a 422 (could not extract text)."""


class RecognizerUnavailable(RecognizerError):
    """The recognizer cannot handle this input *by design* — not configured
    (missing API key) or an unsupported format. The fallback chain skips it
    quietly (debug, not warning) since it never attempted real work."""
