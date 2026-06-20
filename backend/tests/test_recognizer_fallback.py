"""Tests for the fallback recognizer chain."""
import pytest

from app.rag_core.recognizers.base import (
    RecognitionResult,
    RecognizerError,
    RecognizerUnavailable,
)
from app.rag_core.recognizers.fallback import FallbackRecognizer


class Stub:
    """A recognizer that returns text, or raises a configured exception."""

    def __init__(self, *, text=None, raises=None, strategy="stub"):
        self._text = text
        self._raises = raises
        self._strategy = strategy
        self.calls = 0

    async def recognize(self, data, mime_type, filename):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return RecognitionResult(text=self._text, strategy=self._strategy)


async def test_first_non_empty_wins_and_short_circuits():
    primary = Stub(text="A", strategy="sotaocr")
    secondary = Stub(text="B", strategy="llm-file")
    rec = FallbackRecognizer([primary, secondary])
    result = await rec.recognize(b"x", "application/pdf", "a.pdf")
    assert result.text == "A" and result.strategy == "sotaocr"
    assert primary.calls == 1 and secondary.calls == 0


async def test_unavailable_skips_to_next():
    primary = Stub(raises=RecognizerUnavailable("no key"))
    secondary = Stub(text="B", strategy="llm-file")
    rec = FallbackRecognizer([primary, secondary])
    result = await rec.recognize(b"x", "application/pdf", "a.pdf")
    assert result.text == "B"
    assert primary.calls == 1 and secondary.calls == 1


async def test_error_falls_through_to_next():
    primary = Stub(raises=RecognizerError("boom"))
    secondary = Stub(text="B")
    rec = FallbackRecognizer([primary, secondary])
    result = await rec.recognize(b"x", "application/pdf", "a.pdf")
    assert result.text == "B"


async def test_arbitrary_exception_falls_through():
    primary = Stub(raises=RuntimeError("unexpected"))
    secondary = Stub(text="B")
    rec = FallbackRecognizer([primary, secondary])
    result = await rec.recognize(b"x", "application/pdf", "a.pdf")
    assert result.text == "B"


async def test_empty_result_falls_through():
    primary = Stub(text="   ")  # whitespace-only -> treated as empty
    secondary = Stub(text="B")
    rec = FallbackRecognizer([primary, secondary])
    result = await rec.recognize(b"x", "application/pdf", "a.pdf")
    assert result.text == "B"


async def test_all_fail_raises_recognizer_error():
    rec = FallbackRecognizer([
        Stub(raises=RecognizerUnavailable("no key")),
        Stub(raises=RecognizerError("boom")),
    ])
    with pytest.raises(RecognizerError, match="all recognizers failed"):
        await rec.recognize(b"x", "application/pdf", "a.pdf")


def test_empty_chain_rejected():
    with pytest.raises(ValueError):
        FallbackRecognizer([])
