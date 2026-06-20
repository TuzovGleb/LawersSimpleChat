"""Fallback recognizer — try each recognizer in order, first non-empty wins.

This is how SotaOCR degrades to the LLM recognizer: an ``unavailable`` child
(not configured / unsupported format) is skipped quietly; a child that errors or
returns empty is logged and the next is tried. If every child fails, the
accumulated reasons are re-raised as a :class:`RecognizerError` so the endpoint
can map it to a 422.
"""
from __future__ import annotations

import logging

from app.rag_core.recognizers.base import (
    RecognitionResult,
    Recognizer,
    RecognizerError,
    RecognizerUnavailable,
)

logger = logging.getLogger(__name__)


class FallbackRecognizer:
    def __init__(self, chain: list[Recognizer]):
        if not chain:
            raise ValueError("FallbackRecognizer requires at least one recognizer")
        self._chain = chain

    async def recognize(self, data: bytes, mime_type: str, filename: str) -> RecognitionResult:
        reasons: list[str] = []
        for recognizer in self._chain:
            name = type(recognizer).__name__
            try:
                result = await recognizer.recognize(data, mime_type, filename)
            except RecognizerUnavailable as err:
                logger.debug(
                    "recognizer unavailable; skipping",
                    extra={"recognizer": name, "reason": str(err), "doc_filename": filename},
                )
                reasons.append(f"{name}: unavailable ({err})")
                continue
            except Exception as err:  # noqa: BLE001 - logged, then next recognizer is tried
                logger.warning(
                    "recognizer failed; trying next in chain",
                    extra={"recognizer": name, "doc_filename": filename}, exc_info=True,
                )
                reasons.append(f"{name}: {err}")
                continue

            if result.text.strip():
                return result
            logger.warning(
                "recognizer returned empty; trying next in chain",
                extra={"recognizer": name, "doc_filename": filename},
            )
            reasons.append(f"{name}: empty result")

        raise RecognizerError("all recognizers failed: " + "; ".join(reasons))
