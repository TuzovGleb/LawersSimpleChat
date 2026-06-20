"""Tests for the SotaOCR recognizer/client (no network — httpx.MockTransport)."""
import httpx
import pytest

from app.rag_core.recognizers import sotaocr as so
from app.rag_core.recognizers.base import RecognizerError, RecognizerUnavailable
from app.rag_core.recognizers.sotaocr import SotaOcrClient, SotaOcrRecognizer, _supports


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Skip the >=1s poll interval so the tests run instantly."""
    async def _instant(*_a, **_k):
        return None

    monkeypatch.setattr(so.asyncio, "sleep", _instant)


def _client(handler, **kw):
    return SotaOcrClient(
        api_key="k",
        base_url="https://sotaocr.test",
        transport=httpx.MockTransport(handler),
        **kw,
    )


# --- format support ---

def test_supports():
    assert _supports("application/pdf", ".pdf")
    assert _supports("image/png", ".png")
    assert _supports("", ".tiff")
    assert not _supports("application/vnd...wordprocessingml.document", ".docx")
    assert not _supports("image/gif", ".gif")


# --- happy path: submit -> poll(completed) -> result(content) ---

def _happy_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if request.method == "POST" and path == "/v1/extract":
        return httpx.Response(202, json={"id": "job_1", "status": "pending", "page_count": 1})
    if path == "/v1/jobs/job_1":
        return httpx.Response(200, json={"status": "completed", "page_count": 1, "pages_completed": 1})
    if path == "/v1/jobs/job_1/result":
        assert request.url.params.get("format") == "markdown"
        return httpx.Response(200, json={"job_id": "job_1", "format": "markdown", "content": "# Hi\n\nworld"})
    return httpx.Response(404, json={"error": {"code": "not_found"}})


async def test_extract_happy_path():
    text = await _client(_happy_handler).extract(b"%PDF", "a.pdf", "application/pdf")
    assert text == "# Hi\n\nworld"


async def test_recognizer_returns_sotaocr_strategy():
    rec = SotaOcrRecognizer(_client(_happy_handler))
    result = await rec.recognize(b"%PDF", "application/pdf", "a.pdf")
    assert result.strategy == "sotaocr" and "world" in result.text


async def test_submit_sends_model_profile():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen["body"] = request.content
        return _happy_handler(request)

    await _client(handler, model_profile="pro").extract(b"%PDF", "a.pdf", "application/pdf")
    assert b"model_profile" in seen["body"] and b"pro" in seen["body"]


# --- error mapping ---

async def test_unsupported_format_skips_without_http():
    # docx is unsupported -> RecognizerUnavailable, no HTTP attempted.
    def handler(request):
        raise AssertionError("should not hit the network for unsupported format")

    rec = SotaOcrRecognizer(_client(handler))
    with pytest.raises(RecognizerUnavailable):
        await rec.recognize(b"PK", "application/vnd.openxmlformats", "a.docx")


async def test_not_configured_is_unavailable():
    rec = SotaOcrRecognizer(SotaOcrClient(api_key="", base_url="https://sotaocr.test"))
    with pytest.raises(RecognizerUnavailable):
        await rec.recognize(b"%PDF", "application/pdf", "a.pdf")


async def test_insufficient_balance_raises_recognizer_error():
    def handler(request):
        return httpx.Response(403, json={"error": {"code": "insufficient_balance"}})

    with pytest.raises(RecognizerError, match="balance"):
        await _client(handler).extract(b"%PDF", "a.pdf", "application/pdf")


async def test_unsupported_media_on_submit_is_unavailable():
    def handler(request):
        return httpx.Response(415, json={"error": {"code": "unsupported_media"}})

    with pytest.raises(RecognizerUnavailable):
        await _client(handler).extract(b"x", "a.bin", "application/octet-stream")


async def test_empty_content_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST":
            return httpx.Response(202, json={"id": "job_1", "page_count": 1})
        if path == "/v1/jobs/job_1":
            return httpx.Response(200, json={"status": "completed", "page_count": 1, "pages_completed": 1})
        return httpx.Response(200, json={"content": ""})

    rec = SotaOcrRecognizer(_client(handler))
    with pytest.raises(RecognizerError, match="empty"):
        await rec.recognize(b"%PDF", "application/pdf", "a.pdf")


async def test_job_failure_status_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"id": "job_1", "page_count": 1})
        return httpx.Response(200, json={"status": "failed", "page_count": 1, "pages_completed": 0})

    with pytest.raises(RecognizerError, match="failed"):
        await _client(handler).extract(b"%PDF", "a.pdf", "application/pdf")


async def test_job_timeout_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"id": "job_1", "page_count": 5})
        return httpx.Response(200, json={"status": "processing", "page_count": 5, "pages_completed": 1})

    with pytest.raises(RecognizerError, match="timed out"):
        await _client(handler, job_timeout_seconds=0.0).extract(b"%PDF", "a.pdf", "application/pdf")


async def test_result_as_raw_markdown_body():
    # Some responses may return the markdown directly (not wrapped in JSON).
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST":
            return httpx.Response(202, json={"id": "job_1", "page_count": 1})
        if path == "/v1/jobs/job_1":
            return httpx.Response(200, json={"status": "completed", "page_count": 1, "pages_completed": 1})
        return httpx.Response(200, text="# Heading\n\nbody text", headers={"content-type": "text/markdown"})

    text = await _client(handler).extract(b"%PDF", "a.pdf", "application/pdf")
    assert text == "# Heading\n\nbody text"


async def test_completes_on_second_poll():
    polls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST":
            return httpx.Response(202, json={"id": "job_1", "page_count": 2})
        if path == "/v1/jobs/job_1":
            polls["n"] += 1
            done = polls["n"] >= 2
            return httpx.Response(200, json={
                "status": "completed" if done else "processing",
                "page_count": 2, "pages_completed": 2 if done else 1,
            })
        return httpx.Response(200, json={"content": "ok"})

    text = await _client(handler).extract(b"%PDF", "a.pdf", "application/pdf")
    assert text == "ok" and polls["n"] == 2
