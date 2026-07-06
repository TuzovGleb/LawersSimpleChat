"""Tests for the LangChain-backed LLM recognizer (no network)."""
import pytest

from app.rag_core.recognizers import llm as le
from app.rag_core.recognizers.base import RecognizerError
from app.rag_core.recognizers.llm import LlmRecognizer, _extract_content
from app.rag_core.recognizers.pdf_gate import DocGate, PageGate


def fake_gate(pages: list[str | None]) -> DocGate:
    """None => page needs OCR; a string => trusted text-layer content."""
    return DocGate(pages=[
        PageGate(t or "", t is None, 0.0, 0.0, "test") for t in pages
    ])


def install_gate(monkeypatch, pages: list[str | None]) -> None:
    monkeypatch.setattr(
        le, "evaluate_pdf", lambda data, stop_at_monotonic=None: fake_gate(pages)
    )
    monkeypatch.setattr(le._PdfPageSource, "probe", lambda self: None)
    monkeypatch.setattr(
        le._PdfPageSource, "page_bytes", lambda self, i: f"p{i}".encode()
    )


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


async def test_pdf_per_page_ocrs_gated_pages(monkeypatch):
    install_gate(monkeypatch, [None, None])
    llm = FakeLLM(content="PAGE")
    ex = LlmRecognizer(llm)
    per = await ex.pdf_per_page(b"whatever", "scan.pdf")
    assert per.text == "PAGE\n\nPAGE"
    assert (per.pages_total, per.pages_from_layer, per.pages_ocred, per.pages_failed) == (2, 0, 2, 0)
    # each page sent as a type:'file' part
    content = _human_content(llm.invocations[0])
    assert content[1]["type"] == "file"
    assert content[1]["file"]["file_data"].startswith("data:application/pdf;base64,")


async def test_pdf_per_page_all_layer_makes_no_llm_calls(monkeypatch):
    install_gate(monkeypatch, ["First page.", "Second page."])
    llm = FakeLLM()
    ex = LlmRecognizer(llm)
    per = await ex.pdf_per_page(b"x", "doc.pdf")
    assert per.text == "First page.\n\nSecond page."
    assert per.pages_from_layer == 2 and per.pages_ocred == 0
    assert llm.invocations == []


async def test_pdf_per_page_mixed_keeps_page_order(monkeypatch):
    install_gate(monkeypatch, ["Layer one.", None, "Layer three."])
    ex = LlmRecognizer(FakeLLM(content="OCR TWO"))
    per = await ex.pdf_per_page(b"x", "doc.pdf")
    assert per.text == "Layer one.\n\nOCR TWO\n\nLayer three."


async def test_pdf_per_page_unopenable_returns_none(monkeypatch):
    monkeypatch.setattr(le, "evaluate_pdf", lambda data, stop_at_monotonic=None: None)
    ex = LlmRecognizer(FakeLLM())
    assert await ex.pdf_per_page(b"x", "a.pdf") is None


async def test_pdf_per_page_partial_not_all_or_nothing(monkeypatch):
    """One page failing after retries no longer discards the good pages."""
    install_gate(monkeypatch, [None, None])

    class FirstPageAlwaysFails:
        def __init__(self):
            self.invocations = []

        async def ainvoke(self, messages, *a, **k):
            self.invocations.append(messages)
            if "page 1 of" in messages[1].content[0]["text"]:
                raise RuntimeError("provider boom")
            return _AIMessage("PAGE TWO")

    ex = LlmRecognizer(FirstPageAlwaysFails())
    per = await ex.pdf_per_page(b"x", "a.pdf")
    assert per.text == "PAGE TWO"
    assert per.pages_failed == 1 and per.pages_ocred == 1


async def test_single_page_retries_then_succeeds(monkeypatch):
    install_gate(monkeypatch, [None, None])
    # Fail exactly once: the first attempt (any page) retries and then succeeds.
    llm = FakeLLM(content="PAGE", fail_times=1)
    ex = LlmRecognizer(llm)
    per = await ex.pdf_per_page(b"x", "a.pdf")
    assert per.text == "PAGE\n\nPAGE" and per.pages_failed == 0


async def test_pdf_per_page_hard_wall_bounds_a_hung_page(monkeypatch):
    """A page whose LLM call hangs is cut by the per-page wall; the rest survive."""
    import asyncio

    install_gate(monkeypatch, [None, None])
    monkeypatch.setattr(le, "PDF_PAGE_HARD_TIMEOUT", 0.05)
    monkeypatch.setattr(le, "PDF_PAGE_MAX_RETRIES", 0)

    class SecondPageHangs:
        async def ainvoke(self, messages, *a, **k):
            if "page 2 of" in messages[1].content[0]["text"]:
                await asyncio.sleep(3600)
            return _AIMessage("PAGE ONE")

    ex = LlmRecognizer(SecondPageHangs())
    per = await ex.pdf_per_page(b"x", "a.pdf")
    assert per.text == "PAGE ONE"
    assert per.pages_failed == 1


async def test_pdf_per_page_circuit_breaker_stops_feeding_outage(monkeypatch):
    install_gate(monkeypatch, [None] * 40)
    monkeypatch.setattr(le, "PDF_CIRCUIT_BREAKER_THRESHOLD", 5)
    monkeypatch.setattr(le, "PDF_PAGE_MAX_RETRIES", 0)
    llm = FakeLLM(fail_times=999)
    ex = LlmRecognizer(llm)
    per = await ex.pdf_per_page(b"x", "a.pdf")
    assert per.pages_ocred == 0 and per.pages_failed == 40
    # far fewer than 40 calls: the breaker stopped scheduling after ~threshold
    assert len(llm.invocations) < 20


async def test_pdf_per_page_deadline_already_past_saves_partial(monkeypatch):
    import asyncio

    install_gate(monkeypatch, ["Layer page.", None])
    monkeypatch.setattr(le, "extraction_deadline",
                        lambda: asyncio.get_running_loop().time() - 1)
    llm = FakeLLM(content="NEVER")
    ex = LlmRecognizer(llm)
    per = await ex.pdf_per_page(b"x", "a.pdf")
    assert per.text == "Layer page."
    assert per.pages_failed == 1 and llm.invocations == []


async def test_pdf_per_page_deadline_fires_mid_run_keeps_collected_pages(monkeypatch):
    import asyncio

    install_gate(monkeypatch, [None, None])
    monkeypatch.setattr(le, "PDF_DEADLINE_RESERVE", 0.0)
    monkeypatch.setattr(le, "PDF_PAGE_MAX_RETRIES", 0)
    monkeypatch.setattr(le, "extraction_deadline",
                        lambda: asyncio.get_running_loop().time() + 0.3)
    # intercept the module logger directly: caplog misses records once the app's
    # dictConfig (another test) turns propagation off
    warnings: list[str] = []
    monkeypatch.setattr(
        le.logger, "warning", lambda msg, *a, **k: warnings.append(msg % a if a else msg)
    )

    class SecondPageHangs:
        async def ainvoke(self, messages, *a, **k):
            if "page 2 of" in messages[1].content[0]["text"]:
                await asyncio.sleep(3600)
            return _AIMessage("PAGE ONE")

    ex = LlmRecognizer(SecondPageHangs())
    per = await ex.pdf_per_page(b"x", "a.pdf")
    assert per.text == "PAGE ONE"
    assert per.pages_ocred == 1 and per.pages_failed == 1
    # pins the asyncio.timeout_at branch, not the do_page pre-check or the wall
    assert any("extraction deadline reached" in m for m in warnings)


async def test_pdf_per_page_empty_ocr_text_counts_as_failed(monkeypatch):
    install_gate(monkeypatch, [None, None])

    class SecondPageEmpty:
        async def ainvoke(self, messages, *a, **k):
            if "page 2 of" in messages[1].content[0]["text"]:
                return _AIMessage("   ")
            return _AIMessage("PAGE ONE")

    per = await LlmRecognizer(SecondPageEmpty()).pdf_per_page(b"x", "a.pdf")
    assert per.text == "PAGE ONE"
    assert per.pages_ocred == 1 and per.pages_failed == 1  # doc stays truncated


async def test_pdf_per_page_pypdf_unopenable_all_ocr_returns_none(monkeypatch):
    """pdfium-openable but pypdf-unopenable scan -> None -> whole-file fallback."""
    install_gate(monkeypatch, [None, None])

    def boom(self):
        raise ValueError("pypdf cannot parse")

    monkeypatch.setattr(le._PdfPageSource, "probe", boom)
    llm = FakeLLM(content="NEVER")
    assert await LlmRecognizer(llm).pdf_per_page(b"x", "a.pdf") is None
    assert llm.invocations == []


async def test_pdf_per_page_pypdf_unopenable_keeps_layer_pages(monkeypatch):
    install_gate(monkeypatch, ["Layer page.", None, None])

    def boom(self):
        raise ValueError("pypdf cannot parse")

    monkeypatch.setattr(le._PdfPageSource, "probe", boom)
    per = await LlmRecognizer(FakeLLM(content="NEVER")).pdf_per_page(b"x", "a.pdf")
    assert per.text == "Layer page."
    assert (per.pages_from_layer, per.pages_ocred, per.pages_failed) == (1, 0, 2)


# --- recognize() dispatch (image -> vision; pdf -> per-page/file; other -> file) ---

async def test_recognize_image_uses_vision():
    ex = LlmRecognizer(FakeLLM(content="IMG"))
    result = await ex.recognize(b"\x89PNG", "image/png", "scan.png")
    assert result.strategy == "vision" and result.text == "IMG"


async def test_recognize_pdf_uses_per_page(monkeypatch):
    install_gate(monkeypatch, [None, None])
    ex = LlmRecognizer(FakeLLM(content="PAGE"))
    result = await ex.recognize(b"%PDF", "application/pdf", "scan.pdf")
    assert result.strategy == "pdf-pages" and result.text == "PAGE\n\nPAGE"
    assert not result.truncated
    assert (result.pages_total, result.pages_recognized) == (2, 2)


async def test_recognize_pdf_all_layer_strategy(monkeypatch):
    install_gate(monkeypatch, ["Layer one.", "Layer two."])
    ex = LlmRecognizer(FakeLLM())
    result = await ex.recognize(b"%PDF", "application/pdf", "doc.pdf")
    assert result.strategy == "pdf-text-layer"
    assert result.text == "Layer one.\n\nLayer two."


async def test_recognize_pdf_partial_is_truncated(monkeypatch):
    install_gate(monkeypatch, ["Layer page.", None])
    monkeypatch.setattr(le, "PDF_PAGE_MAX_RETRIES", 0)
    ex = LlmRecognizer(FakeLLM(fail_times=999))
    result = await ex.recognize(b"%PDF", "application/pdf", "doc.pdf")
    assert result.strategy == "pdf-pages" and result.truncated
    assert result.text == "Layer page."
    assert (result.pages_total, result.pages_recognized) == (2, 1)


async def test_recognize_pdf_unopenable_falls_back_to_file(monkeypatch):
    monkeypatch.setattr(le, "evaluate_pdf", lambda data, stop_at_monotonic=None: None)
    ex = LlmRecognizer(FakeLLM(content="FILE"))
    result = await ex.recognize(b"%PDF-1.4 broken", "application/pdf", "a.pdf")
    assert result.strategy == "llm-file" and result.text == "FILE"


async def test_recognize_pdf_header_past_offset_zero_still_falls_back(monkeypatch):
    """The spec allows junk before %PDF- within the first 1024 bytes."""
    monkeypatch.setattr(le, "evaluate_pdf", lambda data, stop_at_monotonic=None: None)
    ex = LlmRecognizer(FakeLLM(content="FILE"))
    data = b"\x00" * 512 + b"%PDF-1.7 damaged xref"
    result = await ex.recognize(data, "application/pdf", "broken.pdf")
    assert result.strategy == "llm-file" and result.text == "FILE"


async def test_recognize_pdf_header_at_boundary_offset_still_falls_back(monkeypatch):
    """pdfium accepts a header STARTING at offsets 0..1024 inclusive; the sniff
    must not cut a boundary header mid-token (data[:1024] would)."""
    monkeypatch.setattr(le, "evaluate_pdf", lambda data, stop_at_monotonic=None: None)
    ex = LlmRecognizer(FakeLLM(content="FILE"))
    data = b"\x00" * 1024 + b"%PDF-1.7 damaged"
    result = await ex.recognize(data, "application/pdf", "broken.pdf")
    assert result.strategy == "llm-file" and result.text == "FILE"


async def test_recognize_junk_named_pdf_raises_without_llm_call(monkeypatch):
    """A macOS AppleDouble sidecar named *.PDF must never reach the provider."""
    monkeypatch.setattr(le, "evaluate_pdf", lambda data, stop_at_monotonic=None: None)
    llm = FakeLLM(content="NEVER")
    junk = b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X" + b"\x00" * 64
    with pytest.raises(RecognizerError, match="AppleDouble"):
        await LlmRecognizer(llm).recognize(junk, "application/pdf", "._SCAN0053.PDF")
    assert llm.invocations == []


async def test_recognize_arbitrary_junk_named_pdf_raises(monkeypatch):
    monkeypatch.setattr(le, "evaluate_pdf", lambda data, stop_at_monotonic=None: None)
    llm = FakeLLM(content="NEVER")
    with pytest.raises(RecognizerError, match="no %PDF- header"):
        await LlmRecognizer(llm).recognize(b"not a pdf at all", "application/pdf", "a.pdf")
    assert llm.invocations == []


def _api_status_llm(status: int, message: str):
    """FakeLLM whose ainvoke raises the openai error for the given HTTP status."""
    import httpx
    from openai import APIStatusError, BadRequestError

    cls = BadRequestError if status == 400 else APIStatusError

    class Raises:
        async def ainvoke(self, messages, *a, **k):
            response = httpx.Response(
                status, request=httpx.Request("POST", "https://openrouter.test/chat")
            )
            raise cls(message, response=response, body=None)

    return Raises()


async def test_file_attachment_provider_400_maps_to_recognizer_error():
    """Provider rejecting the input (openai 400) is a recognition failure -> the
    endpoint's RecognizerError->422 mapping, not an unhandled 500."""
    llm = _api_status_llm(400, "The document has no pages.")
    with pytest.raises(RecognizerError, match="provider rejected"):
        await LlmRecognizer(llm).file_attachment(b"%PDF-1.4", "a.pdf")


async def test_file_attachment_provider_413_maps_to_recognizer_error():
    llm = _api_status_llm(413, "Payload too large")
    with pytest.raises(RecognizerError, match="provider rejected"):
        await LlmRecognizer(llm).file_attachment(b"%PDF-1.4", "a.pdf")


async def test_file_attachment_provider_401_propagates_unchanged():
    """Auth/config errors are NOT document failures — they must stay a 500."""
    from openai import APIStatusError

    llm = _api_status_llm(401, "Invalid API key")
    with pytest.raises(APIStatusError):
        await LlmRecognizer(llm).file_attachment(b"%PDF-1.4", "a.pdf")


async def test_recognize_single_page_pdf_text_layer_no_llm_calls(monkeypatch):
    """Pin: 1-page PDFs are gated too (old code short-circuited count<=1 to llm-file)."""
    install_gate(monkeypatch, ["Only page."])
    llm = FakeLLM()
    result = await LlmRecognizer(llm).recognize(b"%PDF", "application/pdf", "one.pdf")
    assert result.strategy == "pdf-text-layer" and result.text == "Only page."
    assert llm.invocations == []


async def test_recognize_single_page_scan_goes_per_page(monkeypatch):
    install_gate(monkeypatch, [None])
    result = await LlmRecognizer(FakeLLM(content="SCAN")).recognize(
        b"%PDF", "application/pdf", "one.pdf"
    )
    assert result.strategy == "pdf-pages" and result.text == "SCAN"
    assert (result.pages_total, result.pages_recognized) == (1, 1)


async def test_recognize_pdf_small_doc_zero_pages_falls_back_to_file(monkeypatch):
    install_gate(monkeypatch, [None, None])
    monkeypatch.setattr(le, "PDF_PAGE_MAX_RETRIES", 0)

    class FailPagesButNotFile:
        async def ainvoke(self, messages, *a, **k):
            if messages[1].content[-1].get("type") == "file":
                raise RuntimeError("provider boom")
            return _AIMessage("WHOLE FILE")

    ex = LlmRecognizer(FailPagesButNotFile())
    result = await ex.recognize(b"%PDF", "application/pdf", "a.pdf")
    assert result.strategy == "llm-file" and result.text == "WHOLE FILE"


async def test_recognize_pdf_large_doc_zero_pages_raises(monkeypatch):
    install_gate(monkeypatch, [None] * 10)
    monkeypatch.setattr(le, "PDF_PAGE_MAX_RETRIES", 0)
    ex = LlmRecognizer(FakeLLM(fail_times=999))
    with pytest.raises(RecognizerError):
        await ex.recognize(b"%PDF", "application/pdf", "big.pdf")


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
