"""Tests for the LangChain-backed LLM recognizer (no network)."""
import pytest

from app.rag_core.recognizers import llm as le
from app.rag_core.recognizers.llm import LlmRecognizer, _extract_content


class _AIMessage:
    def __init__(self, content):
        self.content = content


class FakeLLM:
    """Records invocations; can fail the first N calls to exercise retries."""

    def __init__(self, content="OK", fail_times=0):
        self._content = content
        self._fail = fail_times
        self.invocations: list[list] = []

    async def ainvoke(self, messages, *args, **kwargs):
        self.invocations.append(messages)
        if self._fail > 0:
            self._fail -= 1
            raise RuntimeError("provider boom")
        return _AIMessage(self._content)


def _human_content(messages):
    # messages = [SystemMessage, HumanMessage]; return the human content list.
    return messages[1].content


def test_extract_content_variants():
    assert _extract_content(_AIMessage("  hi ")) == "hi"
    assert _extract_content(_AIMessage([{"type": "text", "text": "a"}, "b", {"x": 1}])) == "a\nb"
    assert _extract_content(_AIMessage(None)) == ""


async def test_vision_builds_image_url():
    llm = FakeLLM(content="VISIONTEXT")
    ex = LlmRecognizer(llm)
    out = await ex.vision(b"\x89PNG", "image/png", "scan.png")
    assert out == "VISIONTEXT"
    content = _human_content(llm.invocations[0])
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_file_attachment_pdf_uses_image_url():
    llm = FakeLLM()
    ex = LlmRecognizer(llm)
    await ex.file_attachment(b"%PDF-1.4", "a.pdf")
    content = _human_content(llm.invocations[0])
    assert any(p.get("type") == "image_url" for p in content)


async def test_file_attachment_text_inlines_utf8():
    llm = FakeLLM()
    ex = LlmRecognizer(llm)
    await ex.file_attachment("привет".encode("utf-8"), "a.rtf")
    content = _human_content(llm.invocations[0])
    assert content[-1]["type"] == "text"
    assert "привет" in content[-1]["text"]


async def test_pdf_per_page_joins_pages(monkeypatch):
    monkeypatch.setattr(le, "_split_pdf_pages_sync", lambda data: [b"p1", b"p2"])
    llm = FakeLLM(content="PAGE")
    ex = LlmRecognizer(llm)
    out = await ex.pdf_per_page(b"whatever", "scan.pdf")
    assert out == "PAGE\n\nPAGE"
    # each page sent as a type:'file' part
    content = _human_content(llm.invocations[0])
    assert content[1]["type"] == "file"
    assert content[1]["file"]["file_data"].startswith("data:application/pdf;base64,")


async def test_pdf_per_page_single_page_returns_none(monkeypatch):
    monkeypatch.setattr(le, "_split_pdf_pages_sync", lambda data: None)
    ex = LlmRecognizer(FakeLLM())
    assert await ex.pdf_per_page(b"x", "a.pdf") is None


async def test_pdf_per_page_all_or_nothing(monkeypatch):
    monkeypatch.setattr(le, "_split_pdf_pages_sync", lambda data: [b"p1", b"p2"])
    # Always fails -> after retries gather raises -> None (fall back to single request)
    ex = LlmRecognizer(FakeLLM(fail_times=999))
    assert await ex.pdf_per_page(b"x", "a.pdf") is None


async def test_single_page_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(le, "_split_pdf_pages_sync", lambda data: [b"only-two-page-doc", b"p2"])
    # Fail exactly once: the first attempt (any page) retries and then succeeds.
    llm = FakeLLM(content="PAGE", fail_times=1)
    ex = LlmRecognizer(llm)
    out = await ex.pdf_per_page(b"x", "a.pdf")
    assert out == "PAGE\n\nPAGE"


# --- recognize() dispatch (image -> vision; pdf -> per-page/file; other -> file) ---

async def test_recognize_image_uses_vision():
    ex = LlmRecognizer(FakeLLM(content="IMG"))
    result = await ex.recognize(b"\x89PNG", "image/png", "scan.png")
    assert result.strategy == "vision" and result.text == "IMG"


async def test_recognize_pdf_uses_per_page(monkeypatch):
    monkeypatch.setattr(le, "_split_pdf_pages_sync", lambda data: [b"p1", b"p2"])
    ex = LlmRecognizer(FakeLLM(content="PAGE"))
    result = await ex.recognize(b"%PDF", "application/pdf", "scan.pdf")
    assert result.strategy == "pdf-pages" and result.text == "PAGE\n\nPAGE"


async def test_recognize_pdf_single_page_falls_back_to_file(monkeypatch):
    monkeypatch.setattr(le, "_split_pdf_pages_sync", lambda data: None)
    ex = LlmRecognizer(FakeLLM(content="FILE"))
    result = await ex.recognize(b"%PDF", "application/pdf", "a.pdf")
    assert result.strategy == "llm-file" and result.text == "FILE"


async def test_recognize_unknown_uses_file_attachment():
    ex = LlmRecognizer(FakeLLM(content="FILE"))
    result = await ex.recognize(b"PK\x03\x04", "application/zip", "a.zip")
    assert result.strategy == "llm-file" and result.text == "FILE"


# --- 429 rate-limit back-off ---

class _RateLimitError(Exception):
    status_code = 429


class _SeqLLM:
    """Raises `exc` the first `fail_times` calls, then returns content."""

    def __init__(self, fail_times, exc):
        self._fail = fail_times
        self._exc = exc

    async def ainvoke(self, messages, *a, **k):
        if self._fail > 0:
            self._fail -= 1
            raise self._exc
        return _AIMessage("OK")


def test_is_rate_limit_detection():
    assert le._is_rate_limit(_RateLimitError("boom"))                       # status_code=429
    assert le._is_rate_limit(RuntimeError("Error code: 429 Too Many Requests"))
    assert le._is_rate_limit(type("RateLimitError", (Exception,), {})("x"))  # class name
    assert not le._is_rate_limit(RuntimeError("provider boom"))


def test_rate_limit_delay_honors_retry_after():
    err = RuntimeError("429")
    err.response = type("R", (), {"headers": {"retry-after": "7"}})()
    assert le._rate_limit_delay(err, 0) == 7.0
    err.response = type("R", (), {"headers": {"retry-after": "999"}})()  # capped
    assert le._rate_limit_delay(err, 0) == le._RATE_LIMIT_MAX_DELAY


async def test_single_page_backs_off_on_429(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(d):
        slept.append(d)

    monkeypatch.setattr(le.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(le.random, "uniform", lambda a, b: b)  # deterministic jitter
    ex = LlmRecognizer(_SeqLLM(fail_times=2, exc=_RateLimitError("429 Too Many Requests")))
    out = await ex._extract_single_page(b"p", "a.pdf", 1, 1)
    assert out == "OK"
    assert len(slept) == 2 and all(d > 0 for d in slept)  # backed off before each retry


async def test_single_page_no_backoff_on_generic_error(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(d):
        slept.append(d)

    monkeypatch.setattr(le.asyncio, "sleep", fake_sleep)
    ex = LlmRecognizer(_SeqLLM(fail_times=1, exc=RuntimeError("provider boom")))
    out = await ex._extract_single_page(b"p", "a.pdf", 1, 1)
    assert out == "OK"
    assert slept == []  # generic errors retry immediately, no back-off
