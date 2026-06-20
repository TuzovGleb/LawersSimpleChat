"""Tests for the recognizer registry/factory (config -> live recognizer)."""
import pytest
from pydantic import ValidationError

from app.rag_core.recognizers.base import Recognizer, RecognizerUnavailable
from app.rag_core.recognizers.factory import describe, get_recognizer
from app.rag_core.recognizers.fallback import FallbackRecognizer
from app.rag_core.recognizers.llm import LlmRecognizer
from app.rag_core.recognizers.sotaocr import SotaOcrRecognizer


def _llm_block():
    return {
        "type": "llm",
        "params": {
            "provider": {"api_key": "test-key", "base_url": "https://openrouter.ai/api/v1"},
            "model": {"name": "google/gemini-3.5-flash", "temperature": 0, "max_tokens": 16384},
        },
    }


def _full_config():
    return {
        "type": "fallback",
        "params": {
            "chain": [
                {"type": "sotaocr", "params": {"api_key": "", "base_url": "https://sotaocr.test"}},
                _llm_block(),
            ]
        },
    }


def test_builds_fallback_chain_from_config():
    rec = get_recognizer(_full_config())
    assert isinstance(rec, FallbackRecognizer)
    assert isinstance(rec, Recognizer)  # runtime_checkable protocol
    # chain order: sotaocr first, llm second
    assert isinstance(rec._chain[0], SotaOcrRecognizer)
    assert isinstance(rec._chain[1], LlmRecognizer)


def test_builds_single_sotaocr():
    rec = get_recognizer({"type": "sotaocr", "params": {"api_key": "k"}})
    assert isinstance(rec, SotaOcrRecognizer)


def test_builds_single_llm():
    rec = get_recognizer(_llm_block())
    assert isinstance(rec, LlmRecognizer)


def test_unknown_type_rejected():
    with pytest.raises(ValidationError):
        get_recognizer({"type": "nope", "params": {}})


def test_describe_renders_chain():
    assert describe(_full_config()) == "sotaocr -> llm"
    assert describe({"type": "sotaocr", "params": {"api_key": "k"}}) == "sotaocr"


async def test_sotaocr_without_key_is_unavailable_at_recognize():
    rec = get_recognizer({"type": "sotaocr", "params": {"api_key": ""}})
    with pytest.raises(RecognizerUnavailable):
        await rec.recognize(b"%PDF", "application/pdf", "a.pdf")
