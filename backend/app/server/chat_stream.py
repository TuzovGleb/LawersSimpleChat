"""Chat orchestration + SSE streaming.

Reproduces the exact wire contract the Next.js frontend expects:
  - ``: heartbeat\\n\\n`` every 5s while the model is working (keeps proxies and
    load balancers from closing the connection);
  - a single final ``data: {message, sessionId, projectId, metadata}\\n\\n`` event;
  - on failure, a single ``data: {error, details}\\n\\n`` event.
"""
import asyncio
import json
import logging
import time
import uuid
from typing import AsyncIterator

from fastapi import Request

from app.server.schema import ChatRequest
from app.services.supabase_repo import SupabaseRepo, unique_document_ids

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 5


def _sse(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


async def _run_graph(request: Request, payload: ChatRequest, session_id: str, project_id: str | None):
    graph = request.app.state.chat_graph
    repo: SupabaseRepo | None = request.app.state.repo

    messages = [m.model_dump() for m in payload.messages]
    documents_by_id: dict[str, dict] = {}
    if repo:
        documents_by_id = await repo.load_attached_documents(project_id, messages)

    state = {
        "history": messages,
        "documents_by_id": documents_by_id,
        "selected_model": payload.selectedModel,
    }
    config = {
        "configurable": {"thread_id": session_id},
        "run_name": "chat",
        "tags": ["chat", payload.selectedModel or "default"],
        "metadata": {
            "session_id": session_id,
            "user_id": payload.userId,
            "project_id": project_id,
            "selected_model": payload.selectedModel,
        },
    }
    return await graph.ainvoke(state, config=config)


async def stream_chat(request: Request, payload: ChatRequest) -> AsyncIterator[bytes]:
    repo: SupabaseRepo | None = request.app.state.repo

    is_new_session = not payload.sessionId
    session_id = payload.sessionId or str(uuid.uuid4())
    started = time.time()

    project_id = payload.projectId
    if not project_id and repo:
        project_id = await repo.resolve_project_id(payload.sessionId)

    # Initial heartbeat so the client sees data immediately.
    yield b": heartbeat\n\n"

    task = asyncio.ensure_future(_run_graph(request, payload, session_id, project_id))
    try:
        while True:
            try:
                result = await asyncio.wait_for(asyncio.shield(task), timeout=HEARTBEAT_INTERVAL_SECONDS)
                break
            except asyncio.TimeoutError:
                yield b": heartbeat\n\n"
    except Exception as error:  # noqa: BLE001 - surface any generation failure to the client
        logger.exception("Chat generation failed", extra={"session_id": session_id})
        yield _sse({"error": "Internal server error", "details": str(error)})
        return

    assistant_message = result.get("response", "")
    metadata = result.get("metadata", {})

    last_user = next((m for m in reversed(payload.messages) if m.role == "user"), None)
    last_user_content = last_user.content if last_user else ""

    if repo:
        if is_new_session:
            created = await repo.create_session(
                session_id=session_id,
                user_id=payload.userId,
                project_id=project_id,
                initial_message=payload.messages[0].content if payload.messages else "",
                utm=payload.utm,
            )
            if not created:
                session_id = None  # surfaced to client as null, matches prior behaviour
        if session_id:
            await repo.save_turn(
                session_id=session_id,
                user_content=last_user_content,
                assistant_content=assistant_message,
                attached_document_ids=unique_document_ids(last_user.model_dump() if last_user else None),
            )

    logger.info(
        "Chat response sent",
        extra={
            "session_id": session_id,
            "project_id": project_id,
            "total_ms": int((time.time() - started) * 1000),
            "model_used": metadata.get("modelUsed"),
        },
    )

    yield _sse(
        {
            "message": assistant_message,
            "sessionId": session_id,
            "projectId": project_id,
            "metadata": metadata,
        }
    )
