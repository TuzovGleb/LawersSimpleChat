"""FastAPI application for the legal chat backend."""
from contextlib import AsyncExitStack, asynccontextmanager
import logging
import os

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.config import CONFIG
from app.pipelines.workflows import build_chat_graph
from app.rag_core.llm import get_chat_registry
from app.rag_core.persistence import CheckpointerConfig, build_checkpointer
from app.server.chat_stream import stream_chat
from app.server.schema import ChatRequest
from app.server.security import verify_backend_secret
from app.services.supabase_repo import SupabaseRepo

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

    async with AsyncExitStack() as stack:
        checkpointer = await build_checkpointer(
            CheckpointerConfig.model_validate(chat_params["persistence"]), stack
        )
        app.state.chat_graph = build_chat_graph(registry, checkpointer)

        supabase_cfg = config["app"].get("supabase") or {}
        if supabase_cfg.get("url") and supabase_cfg.get("service_role_key"):
            app.state.repo = await SupabaseRepo.create(
                supabase_cfg["url"], supabase_cfg["service_role_key"]
            )
            logger.info("Supabase persistence enabled")
        else:
            app.state.repo = None
            logger.warning("Supabase not configured; chat history will not be persisted")

        logger.info("Chat backend ready")
        yield


app = FastAPI(title="LawersSimpleChat Backend", lifespan=lifespan)
app.state.config = CONFIG

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


def get_app() -> FastAPI:
    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
