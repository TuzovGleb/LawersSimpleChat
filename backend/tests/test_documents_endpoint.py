"""Tests for POST /documents/extract (S3 + extraction + Supabase write-back)."""
import asyncio
from collections import defaultdict

import pytest
from fastapi.testclient import TestClient

from app.server.main import app
from app.server.security import verify_backend_secret
from app.services.supabase_repo import map_project_document


class FakeS3:
    def __init__(self, data: bytes):
        self.data = data

    async def download(self, object_key: str) -> bytes:
        return self.data


class FakeRepo:
    def __init__(self, existing=None):
        self.existing = existing
        self.inserted = None
        self.updated = None
        self.touched = None
        self._locks = defaultdict(asyncio.Lock)

    def document_lock(self, object_key):
        return self._locks[object_key]

    async def get_document_by_object_key(self, project_id, object_key):
        return self.existing

    async def insert_project_document(self, record):
        self.inserted = record
        return record

    async def update_project_document_if_improved(
        self, document_id, fields, *, min_raw_text_length
    ):
        # mirror the CAS semantics: only a still-truncated row is replaced, and
        # a partial retry must strictly improve coverage
        if not (self.existing or {}).get("truncated"):
            return None
        if (
            min_raw_text_length is not None
            and (self.existing.get("raw_text_length") or 0) >= min_raw_text_length
        ):
            return None
        self.updated = (document_id, fields)
        return {**self.existing, **fields}

    async def touch_project(self, project_id, user_id, now):
        self.touched = (project_id, user_id, now)


class FakeRecognizer:
    """Recognizer test double: returns text, or raises if text is empty."""

    def __init__(self, text, *, truncated=False, pages_total=None, pages_recognized=None):
        self._text = text
        self._truncated = truncated
        self._pages = (pages_total, pages_recognized)
        self.calls = 0

    async def recognize(self, data, mime_type, filename):
        from app.rag_core.recognizers.base import RecognitionResult, RecognizerError

        self.calls += 1
        if not self._text:
            raise RecognizerError("all recognizers failed (test)")
        return RecognitionResult(
            text=self._text,
            strategy="pdf-pages" if self._truncated else "sotaocr",
            truncated=self._truncated,
            pages_total=self._pages[0],
            pages_recognized=self._pages[1],
        )


@pytest.fixture
def client():
    app.dependency_overrides[verify_backend_secret] = lambda: None
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _payload(**over):
    p = {
        "objectKey": "uploads/proj-1/x/a.txt",
        "filename": "a.txt",
        "mimeType": "text/plain",
        "size": 5,
        "projectId": "proj-1",
        "userId": "user-1",
    }
    p.update(over)
    return p


def test_map_project_document_shape():
    row = {
        "id": "1", "project_id": "p", "name": "n", "mime_type": "text/plain",
        "size": 5, "text": "t", "truncated": False, "raw_text_length": 1,
        "strategy": "pdf", "uploaded_at": "2026-01-01",
    }
    m = map_project_document(row)
    assert m["mimeType"] == "text/plain"
    assert m["rawTextLength"] == 1
    assert m["uploadedAt"] == "2026-01-01"
    assert m["strategy"] == "pdf"


def test_extract_happy_txt(client):
    repo = FakeRepo()
    client.app.state.repo = repo
    client.app.state.s3 = FakeS3(b"hello world")
    r = client.post("/documents/extract", json=_payload())
    assert r.status_code == 201
    doc = r.json()["document"]
    assert doc["text"] == "hello world"
    assert doc["strategy"] == "text"
    assert repo.inserted is not None
    assert repo.inserted["object_key"] == "uploads/proj-1/x/a.txt"
    assert repo.touched is not None


def test_extract_dedup_returns_existing_without_insert(client):
    existing = {
        "id": "e", "project_id": "proj-1", "name": "a.txt", "mime_type": "text/plain",
        "size": 5, "text": "old text", "truncated": False, "raw_text_length": 8,
        "strategy": "text", "uploaded_at": "2026-01-01", "object_key": "uploads/proj-1/x/a.txt",
    }
    repo = FakeRepo(existing=existing)
    client.app.state.repo = repo
    client.app.state.s3 = FakeS3(b"DIFFERENT BYTES")
    r = client.post("/documents/extract", json=_payload())
    assert r.status_code == 200
    assert r.json()["document"]["text"] == "old text"
    assert repo.inserted is None  # idempotent: no second row


def test_extract_empty_text_422(client):
    repo = FakeRepo()
    client.app.state.repo = repo
    client.app.state.s3 = FakeS3(b"PKzipbytes")
    client.app.state.recognizer = FakeRecognizer("")  # every recognizer fails
    r = client.post(
        "/documents/extract",
        json=_payload(objectKey="uploads/proj-1/x/a.zip", filename="a.zip", mimeType="application/zip"),
    )
    assert r.status_code == 422
    assert repo.inserted is None


def test_extract_empty_file_400(client):
    repo = FakeRepo()
    client.app.state.repo = repo
    client.app.state.s3 = FakeS3(b"")
    r = client.post("/documents/extract", json=_payload())
    assert r.status_code == 400


def test_extract_validation_422(client):
    r = client.post("/documents/extract", json={"objectKey": "x"})
    assert r.status_code == 422  # pydantic: missing required fields


def test_extract_with_chat_id_header(client):
    # X-Chat-Id is bound by the middleware and tagged on the extraction trace;
    # the request must still succeed (exercises the chat_id branch).
    repo = FakeRepo()
    client.app.state.repo = repo
    client.app.state.s3 = FakeS3(b"hello with chat")
    r = client.post("/documents/extract", json=_payload(), headers={"X-Chat-Id": "chat-abc"})
    assert r.status_code == 201
    assert r.json()["document"]["text"] == "hello with chat"


# --- partial (truncated) documents stay re-extractable ---

def _truncated_existing(raw_len=100):
    return {
        "id": "e", "project_id": "proj-1", "name": "a.pdf", "mime_type": "application/pdf",
        "size": 5, "text": "x" * raw_len, "truncated": True, "raw_text_length": raw_len,
        "strategy": "pdf-pages", "uploaded_at": "2026-01-01",
        "object_key": "uploads/proj-1/x/a.pdf",
    }


def _pdf_payload():
    return _payload(objectKey="uploads/proj-1/x/a.pdf", filename="a.pdf",
                    mimeType="application/pdf")


def test_truncated_row_skips_dedup_and_reextracts(client):
    repo = FakeRepo(existing=_truncated_existing(raw_len=10))
    client.app.state.repo = repo
    client.app.state.s3 = FakeS3(b"%PDF-fake")
    rec = FakeRecognizer("much longer complete text now", truncated=False,
                         pages_total=3, pages_recognized=3)
    client.app.state.recognizer = rec
    r = client.post("/documents/extract", json=_pdf_payload())
    assert r.status_code == 201
    assert rec.calls == 1, "truncated row must NOT be served from the dedup cache"
    assert repo.inserted is None and repo.updated is not None
    doc_id, fields = repo.updated
    assert doc_id == "e" and fields["truncated"] is False
    assert r.json()["document"]["text"] == "much longer complete text now"
    assert repo.touched is not None


def test_reextract_not_improved_keeps_existing(client):
    repo = FakeRepo(existing=_truncated_existing(raw_len=100))
    client.app.state.repo = repo
    client.app.state.s3 = FakeS3(b"%PDF-fake")
    client.app.state.recognizer = FakeRecognizer(
        "short", truncated=True, pages_total=3, pages_recognized=1
    )
    r = client.post("/documents/extract", json=_pdf_payload())
    assert r.status_code == 200
    assert r.json()["document"]["text"] == "x" * 100  # stored partial kept
    assert repo.updated is None and repo.inserted is None


def test_reextract_failure_returns_stored_partial_not_422(client):
    repo = FakeRepo(existing=_truncated_existing(raw_len=100))
    client.app.state.repo = repo
    client.app.state.s3 = FakeS3(b"%PDF-fake")
    client.app.state.recognizer = FakeRecognizer("")  # retry-into-outage: all fail
    r = client.post("/documents/extract", json=_pdf_payload())
    assert r.status_code == 200, "a failed retry must not hide the stored partial behind a 422"
    assert r.json()["document"]["text"] == "x" * 100
    assert repo.updated is None and repo.inserted is None


def test_reextract_lost_cas_race_returns_stored_row(client):
    # The in-memory snapshot says "improve", but the CAS update reports the row
    # changed meanwhile (another instance won): endpoint returns the stored row.
    repo = FakeRepo(existing=_truncated_existing(raw_len=10))

    async def lost_race(document_id, fields, *, min_raw_text_length):
        return None

    repo.update_project_document_if_improved = lost_race
    client.app.state.repo = repo
    client.app.state.s3 = FakeS3(b"%PDF-fake")
    client.app.state.recognizer = FakeRecognizer("winner text", truncated=False)
    r = client.post("/documents/extract", json=_pdf_payload())
    assert r.status_code == 200
    assert r.json()["document"]["text"] == "x" * 10
    assert repo.inserted is None


def test_extract_sets_extraction_deadline_for_recognizer(client):
    # Deleting begin_extraction_deadline() in main.py must be a test failure,
    # not a silent fallback to the recognizer-local budget.
    from app.services.document_extraction import PDF_EXTRACTION_HARD_BUDGET, extraction_deadline

    seen = {}

    class DeadlineCapturingRecognizer:
        async def recognize(self, data, mime_type, filename):
            from app.rag_core.recognizers.base import RecognitionResult

            seen["deadline"] = extraction_deadline()
            seen["now"] = asyncio.get_running_loop().time()
            return RecognitionResult(text="ok", strategy="sotaocr")

    client.app.state.repo = FakeRepo()
    client.app.state.s3 = FakeS3(b"%PDF-fake")
    client.app.state.recognizer = DeadlineCapturingRecognizer()
    r = client.post("/documents/extract", json=_pdf_payload())
    assert r.status_code == 201
    assert seen["deadline"] is not None, "endpoint must call begin_extraction_deadline()"
    assert 0 < seen["deadline"] - seen["now"] <= PDF_EXTRACTION_HARD_BUDGET + 1
