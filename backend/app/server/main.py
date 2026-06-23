"""FastAPI application for the legal chat backend."""
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
import logging
import os
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.tracers.langchain import wait_for_all_tracers
from langsmith.run_helpers import trace as langsmith_trace

from app.config import CONFIG
from app.pipelines.tools import handlers_of, load_tool_specs
from app.pipelines.workflows import build_chat_graph
from app.rag_core.llm import get_chat_registry
from app.rag_core.recognizers.base import RecognizerError
from app.rag_core.recognizers.factory import describe as describe_recognizer, get_recognizer
from app.server.chat_stream import stream_chat
from app.server.schema import ChatRequest, DocumentExtractRequest
from app.server.security import verify_backend_secret
from app.services.document_extraction import extract_text_from_document
from app.services.s3_client import S3Client
from app.services.supabase_repo import SupabaseRepo, map_project_document
from app.utils import RequestContextMiddleware, current_chat_id, current_request_id

logger = logging.getLogger(__name__)

SSE_HEADERS = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _log_langsmith_state() -> None:
    enabled = os.environ.get("LANGSMITH_TRACING", "").lower() == "true" or os.environ.get(
        "LANGCHAIN_TRACING_V2", ""
    ).lower() == "true"
    if enabled and (os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY")):
        project = os.environ.get("LANGSMITH_PROJECT") or os.environ.get("LANGCHAIN_PROJECT") or "default"
        logger.info("LangSmith tracing enabled", extra={"langsmith_project": project})
    else:
        logger.info("LangSmith tracing disabled (set LANGSMITH_TRACING=true + LANGSMITH_API_KEY to enable)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = app.state.config
    _log_langsmith_state()

    chat_params = config["chat"]["params"]
    registry = get_chat_registry(chat_params)

    # Document OCR/recognition: a config-driven recognizer chain (see
    # recognition.yaml). Default is sotaocr -> llm fallback.
    recognizer_cfg = config["recognition"]["recognizer"]
    app.state.recognizer = get_recognizer(recognizer_cfg)
    logger.info(
        "Document recognizer ready", extra={"recognizer_chain": describe_recognizer(recognizer_cfg)}
    )

    tool_specs = load_tool_specs(config["app"])
    app.state.tool_handlers = handlers_of(tool_specs)
    app.state.chat_graph = build_chat_graph(registry, tool_specs)

    supabase_cfg = config["app"].get("supabase") or {}
    if supabase_cfg.get("url") and supabase_cfg.get("service_role_key"):
        app.state.repo = await SupabaseRepo.create(
            supabase_cfg["url"], supabase_cfg["service_role_key"]
        )
        logger.info("Supabase persistence enabled")
    else:
        app.state.repo = None
        logger.warning("Supabase not configured; chat history will not be persisted")

    s3_cfg = config["app"].get("s3") or {}
    if s3_cfg.get("bucket") and s3_cfg.get("access_key_id") and s3_cfg.get("secret_access_key"):
        app.state.s3 = S3Client(
            bucket=s3_cfg["bucket"],
            access_key_id=s3_cfg["access_key_id"],
            secret_access_key=s3_cfg["secret_access_key"],
            endpoint_url=s3_cfg.get("endpoint_url") or "https://storage.yandexcloud.net",
            region=s3_cfg.get("region") or "ru-central1",
        )
        logger.info("S3 document storage enabled", extra={"s3_bucket": s3_cfg["bucket"]})
    else:
        app.state.s3 = None
        logger.warning("S3 not configured; document extraction from object storage disabled")

    logger.info("Chat backend ready")
    try:
        yield
    finally:
        # Flush queued LangSmith run events before the worker exits so traces
        # aren't orphaned (left spinning) on a graceful shutdown / container
        # recycle. Best-effort: a hard SIGKILL still can't be helped here.
        # SERVERLESS NOTE: needed because Yandex Serverless recycles instances
        # aggressively; on a normal long-lived server the LangSmith background
        # thread flushes on its own and this hook would be redundant.
        with suppress(Exception):
            wait_for_all_tracers()


app = FastAPI(title="LawersSimpleChat Backend", lifespan=lifespan)
app.state.config = CONFIG

# Bind chat_id/request_id for every request so all log lines carry them (added
# last => outermost => runs first, before CORS and the endpoints).
app.add_middleware(RequestContextMiddleware)

_cors_origins = (CONFIG["app"].get("cors_origins") or "").strip()
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in _cors_origins.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/chats/{chat_id}/messages", dependencies=[Depends(verify_backend_secret)])
async def get_chat_messages(request: Request, chat_id: str) -> dict:
    repo: SupabaseRepo | None = request.app.state.repo
    if not repo:
        raise HTTPException(status_code=503, detail="Persistence not configured")
    return {"messages": await repo.get_messages(chat_id)}


@app.post("/chats/{chat_id}/messages", dependencies=[Depends(verify_backend_secret)])
async def post_chat_message(request: Request, chat_id: str, payload: ChatRequest):
    return StreamingResponse(stream_chat(request, chat_id, payload), headers=SSE_HEADERS)


@app.post("/documents/extract", dependencies=[Depends(verify_backend_secret)])
async def extract_document(request: Request, payload: DocumentExtractRequest) -> JSONResponse:
    s3: S3Client | None = request.app.state.s3
    repo: SupabaseRepo | None = request.app.state.repo
    recognizer = request.app.state.recognizer
    if not s3:
        raise HTTPException(status_code=503, detail="S3 not configured")
    if not repo:
        raise HTTPException(status_code=503, detail="Persistence not configured")

    log_ctx = {"project_id": payload.projectId, "object_key": payload.objectKey}

    # Serialize per object_key so a client retry can't double-extract/double-insert.
    async with repo.document_lock(payload.objectKey):
        existing = await repo.get_document_by_object_key(payload.projectId, payload.objectKey)
        if existing:
            logger.info("Document already extracted; returning existing", extra=log_ctx)
            return JSONResponse(content={"document": map_project_document(existing)})

        logger.info("Downloading document from S3", extra={**log_ctx, "size": payload.size})
        data = await s3.download(payload.objectKey)
        if not data:
            raise HTTPException(status_code=400, detail="Empty file cannot be processed")

        # Wrap the whole extraction in ONE LangSmith run so the recognizer's calls
        # (SotaOCR tool run / per-page OCR ChatOpenAI calls) nest as children (one
        # tree per document, no flooding). Nested LangChain calls auto-attach via
        # langchain<->langsmith contextvar interop, which also propagates into the
        # asyncio page tasks. No-op when tracing off.
        chat_id = current_chat_id()  # bound from X-Chat-Id by RequestContextMiddleware
        with langsmith_trace(
            name="document_extraction",
            run_type="chain",
            tags=["document_extraction", chat_id] if chat_id else ["document_extraction"],
            metadata={
                "project_id": payload.projectId,
                "object_key": payload.objectKey,
                "filename": payload.filename,
                "mime_type": payload.mimeType,
                "request_id": current_request_id(),
                "chat_id": chat_id,
            },
        ):
            try:
                extraction = await extract_text_from_document(
                    data, payload.mimeType, payload.filename, recognizer
                )
            except RecognizerError as err:
                # Every recognizer in the chain failed (or returned nothing).
                logger.warning("Recognition failed", extra={**log_ctx, "reason": str(err)})
                raise HTTPException(
                    status_code=422, detail="Could not extract text from document"
                ) from err
        if not extraction.text:
            raise HTTPException(status_code=422, detail="Could not extract text from document")

        now = datetime.now(timezone.utc).isoformat()
        record = {
            "id": str(uuid4()),
            "project_id": payload.projectId,
            "name": payload.filename,
            "mime_type": payload.mimeType,
            "size": payload.size,
            "text": extraction.text,
            "truncated": extraction.truncated,
            "raw_text_length": extraction.raw_text_length,
            "strategy": extraction.strategy,
            "uploaded_at": now,
            "checksum": None,
            "created_at": now,
            "object_key": payload.objectKey,
        }
        row = await repo.insert_project_document(record)
        await repo.touch_project(payload.projectId, payload.userId, now)
        logger.info(
            "Document processed",
            extra={
                **log_ctx,
                "strategy": extraction.strategy,
                "raw_text_length": extraction.raw_text_length,
                "doc_filename": payload.filename,
            },
        )
        return JSONResponse(status_code=201, content={"document": map_project_document(row)})


def get_app() -> FastAPI:
    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
