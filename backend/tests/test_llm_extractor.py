"""Tests for the LangChain-backed LLM document extractor (no network)."""
import base64

import pytest

from app.services import llm_extractor as le
from app.services.llm_extractor import LlmDocumentExtractor, _extract_content


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
    ex = LlmDocumentExtractor(llm)
    out = await ex.vision(b"\x89PNG", "image/png", "scan.png")
    assert out == "VISIONTEXT"
    content = _human_content(llm.invocations[0])
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_file_attachment_pdf_uses_image_url():
    llm = FakeLLM()
    ex = LlmDocumentExtractor(llm)
    await ex.file_attachment(b"%PDF-1.4", "a.pdf")
    content = _human_content(llm.invocations[0])
    assert any(p.get("type") == "image_url" for p in content)


async def test_file_attachment_text_inlines_utf8():
    llm = FakeLLM()
    ex = LlmDocumentExtractor(llm)
    await ex.file_attachment("привет".encode("utf-8"), "a.rtf")
    content = _human_content(llm.invocations[0])
    assert content[-1]["type"] == "text"
    assert "привет" in content[-1]["text"]


async def test_pdf_per_page_joins_pages(monkeypatch):
    monkeypatch.setattr(le, "_split_pdf_pages_sync", lambda data: [b"p1", b"p2"])
    llm = FakeLLM(content="PAGE")
    ex = LlmDocumentExtractor(llm)
    out = await ex.pdf_per_page(b"whatever", "scan.pdf")
    assert out == "PAGE\n\nPAGE"
    # each page sent as a type:'file' part
    content = _human_content(llm.invocations[0])
    assert content[1]["type"] == "file"
    assert content[1]["file"]["file_data"].startswith("data:application/pdf;base64,")


async def test_pdf_per_page_single_page_returns_none(monkeypatch):
    monkeypatch.setattr(le, "_split_pdf_pages_sync", lambda data: None)
    ex = LlmDocumentExtractor(FakeLLM())
    assert await ex.pdf_per_page(b"x", "a.pdf") is None


async def test_pdf_per_page_all_or_nothing(monkeypatch):
    monkeypatch.setattr(le, "_split_pdf_pages_sync", lambda data: [b"p1", b"p2"])
    # Always fails -> after retries gather raises -> None (fall back to single request)
    ex = LlmDocumentExtractor(FakeLLM(fail_times=999))
    assert await ex.pdf_per_page(b"x", "a.pdf") is None


async def test_single_page_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(le, "_split_pdf_pages_sync", lambda data: [b"only-two-page-doc", b"p2"])
    # Fail exactly once: the first attempt (any page) retries and then succeeds.
    llm = FakeLLM(content="PAGE", fail_times=1)
    ex = LlmDocumentExtractor(llm)
    out = await ex.pdf_per_page(b"x", "a.pdf")
    assert out == "PAGE\n\nPAGE"
