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
        self.touched = None
        self._locks = defaultdict(asyncio.Lock)

    def document_lock(self, object_key):
        return self._locks[object_key]

    async def get_document_by_object_key(self, project_id, object_key):
        return self.existing

    async def insert_project_document(self, record):
        self.inserted = record
        return record

    async def touch_project(self, project_id, user_id, now):
        self.touched = (project_id, user_id, now)


class FakeRecognizer:
    """Recognizer test double: returns text, or raises if text is empty."""

    def __init__(self, text):
        self._text = text

    async def recognize(self, data, mime_type, filename):
        from app.rag_core.recognizers.base import RecognitionResult, RecognizerError

        if not self._text:
            raise RecognizerError("all recognizers failed (test)")
        return RecognitionResult(text=self._text, strategy="sotaocr")


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
