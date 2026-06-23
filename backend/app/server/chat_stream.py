"""Chat orchestration + SSE streaming.

Wire contract the Next.js frontend consumes:
  - ``: heartbeat\\n\\n`` whenever the model is working but nothing new has been
    produced for HEARTBEAT_INTERVAL_SECONDS (keeps proxies / load balancers from
    closing the connection during a slow tool call);
  - ``data: {"type": "status", "tool", "label"}`` when the agent starts a tool,
    so the user sees "Ищу судебную практику…" instead of silence;
  - ``data: {"type": "token", "delta"}`` for each chunk of the answer as it
    streams from the model;
  - a single ``data: {"type": "final", "message", "sessionId", "projectId",
    "metadata"}`` event at the end (also carries ``message`` so an older client
    that only looks at ``data.message`` still works);
  - on failure, a single ``data: {"type": "error", "error", "details"}`` event.

The graph is consumed INLINE via ``graph.astream`` rather than a detached
``asyncio.shield``-ed background task: on a client disconnect the stream is
cancelled, which lets LangChain emit the run-end events and close the LangSmith
run instead of orphaning it ("spinner forever"). ``asyncio.shield`` is used only
so a heartbeat timeout cannot cancel the in-flight model call.

SERVERLESS NOTE — why this whole file looks the way it does:
We run on Yandex Serverless Containers, which (1) FREEZE/RECLAIM the instance's
CPU the moment the HTTP response ends, and (2) BUFFER the entire response body
(3.5 MB cap) instead of streaming it out. On a normal always-on server NONE of
the gymnastics below would be needed:
  * No frozen CPU -> the old fire-and-forget ``asyncio.shield``-ed ``ainvoke``
    would simply finish on its own and close the LangSmith run, so the "trace
    hangs forever" bug wouldn't exist and the cancel-then-flush dance here would
    be pointless.
  * No response buffering -> the ``token`` / ``status`` events below would reach
    the browser LIVE (real typewriter + "Ищу практику…" statuses). On serverless
    they're correct on the wire but the platform holds them until the turn ends,
    so the client effectively only sees the final answer. Moving the backend to
    a regular VM would make true streaming work with ZERO changes to this code.
"""
import asyncio
import contextlib
import json
import logging
import time
from typing import AsyncIterator

from fastapi import Request
from langchain_core.messages import AIMessage
from langchain_core.tracers.langchain import wait_for_all_tracers

from app.pipelines.messages import messages_to_rows, split_generated, text_of
from app.server.schema import ChatRequest
from app.services.supabase_repo import SupabaseRepo, unique_document_ids

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 5

# Tool name -> user-facing status shown while the tool runs. Unknown tools fall
# back to DEFAULT_TOOL_STATUS so a newly added tool still surfaces *something*.
TOOL_STATUS_LABELS = {
    "search_court_practice": "Ищу судебную практику…",
    "get_court_decision": "Открываю решение…",
}
DEFAULT_TOOL_STATUS = "Работаю с источниками…"

# Sentinel so a StopAsyncIteration never has to cross an asyncio.wait_for / Task
# boundary (which would otherwise surface as a confusing RuntimeError).
_STREAM_DONE = object()


def _sse(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


async def _anext_or_done(agen):
    try:
        return await agen.__anext__()
    except StopAsyncIteration:
        return _STREAM_DONE


def _token_delta(chunk) -> str:
    content = getattr(chunk, "content", None)
    return text_of(content) if content else ""


async def _assemble_history(
    repo: SupabaseRepo | None,
    payload: ChatRequest,
    session_id: str,
    is_new_session: bool,
) -> tuple[list[dict], str]:
    """Build the conversation history server-side.

    The backend owns history: for an existing session it is loaded from the DB
    and only the new user message is taken from the request. Client-provided
    history is used only for new sessions or as a fallback when the DB is
    unavailable. Returns (history, source) where source is for logging.
    """
    client_messages = [m.model_dump() for m in payload.messages]
    if not repo or is_new_session:
        return client_messages, "client"

    stored = await repo.load_history(session_id)
    if stored is None:
        return client_messages, "client-fallback"

    last_user = next((m for m in reversed(client_messages) if m.get("role") == "user"), None)
    history = stored + ([last_user] if last_user else [])

    # Compare only user-facing rows; tool rows and intermediate assistant
    # messages exist server-side but the client never sees them.
    stored_display = sum(
        1
        for row in stored
        if row.get("role") == "user"
        or (row.get("role") == "assistant" and not row.get("tool_calls"))
    )
    if stored_display != len(client_messages) - 1:
        logger.warning(
            "Server history differs from client history",
            extra={
                "session_id": session_id,
                "stored_display_messages": stored_display,
                "client_messages": len(client_messages),
            },
        )
    return history, "server"


async def _prepare_graph_run(
    request: Request,
    payload: ChatRequest,
    session_id: str,
    project_id: str | None,
    history: list[dict],
):
    """Load attached documents and assemble (graph, state, config) for astream."""
    graph = request.app.state.chat_graph
    repo: SupabaseRepo | None = request.app.state.repo

    documents_by_id: dict[str, dict] = {}
    if repo:
        documents_by_id = await repo.load_attached_documents(project_id, history)

    state = {
        "history": history,
        "documents_by_id": documents_by_id,
        "selected_model": payload.selectedModel,
    }
    config = {
        "configurable": {"thread_id": session_id},
        "run_name": "chat",
        "tags": ["chat", payload.selectedModel or "default", session_id],
        "metadata": {
            "session_id": session_id,
            "user_id": payload.userId,
            "project_id": project_id,
            "selected_model": payload.selectedModel,
        },
    }
    return graph, state, config


async def stream_chat(request: Request, chat_id: str, payload: ChatRequest) -> AsyncIterator[bytes]:
    repo: SupabaseRepo | None = request.app.state.repo

    session_id = chat_id
    started = time.time()

    is_new_session = not await repo.session_exists(session_id) if repo else True

    project_id = payload.projectId
    if not project_id and repo:
        project_id = await repo.resolve_project_id(session_id)

    history, history_source = await _assemble_history(repo, payload, session_id, is_new_session)
    logger.info(
        "History assembled",
        extra={
            "session_id": session_id,
            "history_source": history_source,
            "history_len": len(history),
            "is_new_session": is_new_session,
        },
    )

    # Create the session row up front, before generation runs. This keeps the
    # chat resolvable on a page refresh while the first answer is still
    # streaming (or if generation later fails) instead of 404-ing until the turn
    # completes. Messages are still persisted at the end of the turn.
    if repo and is_new_session:
        created = await repo.create_session(
            session_id=session_id,
            user_id=payload.userId,
            project_id=project_id,
            initial_message=payload.messages[0].content if payload.messages else "",
            utm=payload.utm,
        )
        if not created:
            session_id = None  # surfaced to client as null, matches prior behaviour

    # Initial heartbeat so the client sees data immediately.
    yield b": heartbeat\n\n"

    graph, state, config = await _prepare_graph_run(request, payload, chat_id, project_id, history)

    result: dict | None = None
    announced_tool_calls: set[str] = set()
    needs_flush = False

    # stream_mode=["messages", "values"]:
    #   - "messages" yields (AIMessageChunk, metadata) for token deltas;
    #   - "values" yields the full state after each node — used to (a) announce
    #     a tool BEFORE it runs (the generate snapshot carries the tool_calls)
    #     and (b) capture the final state (== what graph.ainvoke would return).
    agen = graph.astream(state, config=config, stream_mode=["messages", "values"])
    pending = asyncio.ensure_future(_anext_or_done(agen))
    try:
        while True:
            try:
                item = await asyncio.wait_for(
                    asyncio.shield(pending), timeout=HEARTBEAT_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                # Still working (e.g. a slow OpenSearch tool); keep the connection
                # warm WITHOUT cancelling the shielded in-flight step.
                yield b": heartbeat\n\n"
                continue

            if item is _STREAM_DONE:
                break

            mode, data = item
            if mode == "messages":
                chunk, meta = data
                if meta.get("langgraph_node") == "generate":
                    delta = _token_delta(chunk)
                    if delta:
                        # SERVERLESS NOTE: on a normal server this delta reaches
                        # the browser live (typewriter); on Yandex Serverless the
                        # response is buffered, so the client only sees it flushed
                        # at the end. Same code works fully on a VM.
                        yield _sse({"type": "token", "delta": delta})
            elif mode == "values":
                result = data
                messages = data.get("messages") or []
                if messages:
                    for call in getattr(messages[-1], "tool_calls", None) or []:
                        key = call.get("id") or f"{call.get('name')}:{len(announced_tool_calls)}"
                        if key in announced_tool_calls:
                            continue
                        announced_tool_calls.add(key)
                        name = call.get("name", "")
                        yield _sse(
                            {
                                "type": "status",
                                "tool": name,
                                "label": TOOL_STATUS_LABELS.get(name, DEFAULT_TOOL_STATUS),
                            }
                        )

            pending = asyncio.ensure_future(_anext_or_done(agen))
    except asyncio.CancelledError:
        # Client disconnected (closed tab / network drop). Let the cancellation
        # propagate into the model call so its run-end events are emitted, then
        # flush below — instead of orphaning the LangSmith run.
        # SERVERLESS NOTE: this branch only matters because the serverless
        # instance is frozen right after the response ends, so we must close +
        # flush the trace NOW. On a normal server the run would close on its own.
        needs_flush = True
        logger.info("Chat stream cancelled (client disconnected)", extra={"session_id": session_id})
        raise
    except Exception as error:  # noqa: BLE001 - surface any generation failure to the client
        needs_flush = True
        logger.exception("Chat generation failed", extra={"session_id": session_id})
        yield _sse({"type": "error", "error": "Internal server error", "details": str(error)})
        return
    finally:
        # Cancel/await the in-flight __anext__ so aclose() doesn't trip over a
        # running generator, then finalize the graph stream.
        if not pending.done():
            pending.cancel()
        with contextlib.suppress(BaseException):
            await pending
        with contextlib.suppress(Exception):
            await agen.aclose()
        if needs_flush:
            # Force the LangSmith background queue to upload the (error / cancelled)
            # run-end events before this serverless instance is frozen/reclaimed.
            with contextlib.suppress(Exception):
                await asyncio.to_thread(wait_for_all_tracers)

    if result is None:
        return

    assistant_message = result.get("response", "")
    if not assistant_message:
        for message in reversed(result.get("messages") or []):
            if isinstance(message, AIMessage):
                content = message.content
                if isinstance(content, str) and content.strip():
                    assistant_message = content
                    break
                if isinstance(content, list):
                    text_parts = [
                        part.get("text", "")
                        for part in content
                        if isinstance(part, dict) and part.get("type") == "text"
                    ]
                    joined = "".join(text_parts).strip()
                    if joined:
                        assistant_message = joined
                        break

    metadata = result.get("metadata", {}) or {}
    if "toolCallsCount" not in metadata:
        metadata["toolCallsCount"] = result.get("tool_rounds", 0)

    last_user = next((m for m in reversed(payload.messages) if m.role == "user"), None)
    last_user_content = last_user.content if last_user else ""

    if repo:
        if session_id:
            handlers = getattr(request.app.state, "tool_handlers", {}) or {}
            generated = split_generated(result.get("messages") or [])
            user_row = {
                "role": "user",
                "content": last_user_content,
                "attached_document_ids": unique_document_ids(
                    last_user.model_dump() if last_user else None
                ),
            }
            rows = [user_row] + await messages_to_rows(generated, handlers)
            await repo.save_messages(session_id, rows)

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
            "type": "final",
            "message": assistant_message,
            "sessionId": session_id,
            "projectId": project_id,
            "metadata": metadata,
        }
    )
