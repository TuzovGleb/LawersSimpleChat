"""Chat orchestration + SSE streaming.

Wire contract the Next.js frontend consumes:
  - ``: heartbeat\\n\\n`` whenever the model is working but nothing new has been
    produced for HEARTBEAT_INTERVAL_SECONDS (keeps proxies / load balancers from
    closing the connection during a slow tool call);
  - ``data: {"type": "status", "label"}`` when the agent starts a tool, so the
    user sees "Ищу судебную практику…" instead of silence (the raw internal tool
    name is deliberately NOT sent — see prompt.py section [12]);
  - ``data: {"type": "token", "delta"}`` for each chunk of the answer as it
    streams from the model;
  - a single ``data: {"type": "final", "message", "sessionId", "projectId",
    "metadata"}`` event at the end (also carries ``message`` so an older client
    that only looks at ``data.message`` still works); ``metadata`` is vendor-neutral
    (no provider/raw model id — see nodes._extract_metadata);
  - on failure, a single ``data: {"type": "error", "error"}`` event with a
    generic message only (the raw exception is logged server-side, never sent).

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
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tracers.langchain import wait_for_all_tracers

from app.pipelines.messages import messages_to_rows, split_generated, text_of
from app.pipelines.tools.drafting import DRAFT_TOOL_NAME
from app.server.schema import ChatRequest
from app.services.supabase_repo import SupabaseRepo, unique_document_ids

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 5

# Tool name -> user-facing status shown while the tool runs. Unknown tools fall
# back to DEFAULT_TOOL_STATUS so a newly added tool still surfaces *something*.
TOOL_STATUS_LABELS = {
    "search_court_practice": "Ищу судебную практику…",
    "get_court_decision": "Открываю решение…",
    "draft_document": "Готовлю документ…",
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


def _draft_artifacts(messages: list) -> list[dict]:
    """Build downloadable-document artifacts from a turn's draft_document calls.

    Mirrors the reload path (SupabaseRepo._draft_artifact): file_name/status come
    from the tool's JSON result (the same {status, file_name, blocks} payload
    DraftHandler persists). The frontend turns ``id`` into the download URL.
    """
    draft_call_ids = {
        call.get("id")
        for message in messages
        if isinstance(message, AIMessage)
        for call in (getattr(message, "tool_calls", None) or [])
        if call.get("name") == DRAFT_TOOL_NAME and call.get("id")
    }
    artifacts: list[dict] = []
    for message in messages:
        if isinstance(message, ToolMessage) and message.tool_call_id in draft_call_ids:
            try:
                data = json.loads(text_of(message.content))
            except (json.JSONDecodeError, TypeError):
                data = {}
            artifacts.append(
                {
                    "id": message.tool_call_id,
                    "kind": "docx",
                    "fileName": data.get("file_name") or "Документ",
                    "status": data.get("status") or "failed",
                }
            )
    return artifacts


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

    # Compare only user-facing rows. Tool rows and intermediate assistant
    # messages are context-only, BUT a draft turn's note (an assistant row that
    # carries a draft_document call) IS shown — count it like get_messages does,
    # else this diagnostic logs spurious "history differs" warnings.
    def _is_displayed(row: dict) -> bool:
        if row.get("role") == "user":
            return True
        if row.get("role") != "assistant":
            return False
        calls = row.get("tool_calls") or []
        if not calls:
            return True
        return any(isinstance(c, dict) and c.get("name") == DRAFT_TOOL_NAME for c in calls)

    stored_display = sum(1 for row in stored if _is_displayed(row))
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

    # Cross-tenant guard: an existing session must belong to the caller. This is
    # enforced here (backend, service-role) because the BFF's user-scoped client
    # can't see another tenant's session under RLS, yet this path uses the service
    # role and would otherwise append to it. New sessions have no owner yet.
    if repo and not is_new_session and await repo.is_foreign_session(session_id, payload.userId):
        logger.warning(
            "Rejected cross-tenant chat access",
            extra={"session_id": session_id, "user_id": payload.userId},
        )
        yield _sse({"type": "error", "error": "Чат не найден или нет доступа."})
        return

    project_id = payload.projectId
    if not project_id and repo:
        project_id = await repo.resolve_project_id(session_id)

    history, history_source = await _assemble_history(repo, payload, session_id, is_new_session)
    logger.info(
        "History assembled",
        extra={
            "session_id": session_id,
            "user_id": payload.userId,
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
    tokens_sent = 0  # how far the answer got — used to make a 499/cancel diagnosable
    last_tool: str | None = None

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
                        tokens_sent += 1
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
                        last_tool = name
                        # Only the human-readable label goes to the client. The raw
                        # internal tool name (search_court_practice / draft_document…)
                        # would disclose the system's internal machinery, so it is
                        # kept server-side (see prompt.py section [12]).
                        yield _sse(
                            {
                                "type": "status",
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
        # Make the 499/cancel diagnosable: how long the turn ran and where it was
        # when the client vanished (request_id/chat_id are already attached by
        # RequestContextMiddleware). Big elapsed_s with tokens_sent>0 ⇒ a long
        # turn likely dropped by an idle timeout on the buffered connection;
        # near-zero elapsed_s ⇒ the user just navigated away.
        phase = (
            "streaming_answer" if tokens_sent
            else f"tool:{last_tool}" if last_tool
            else "thinking"
        )
        logger.info(
            "Chat stream cancelled (client disconnected)",
            extra={
                "session_id": session_id,
                "elapsed_s": round(time.time() - started, 1),
                "phase": phase,
                "tokens_sent": tokens_sent,
                "tool_rounds": (result or {}).get("tool_rounds", 0),
            },
        )
        raise
    except Exception as error:  # noqa: BLE001 - surface any generation failure to the client
        needs_flush = True
        # Log the full exception server-side (str(error) can embed upstream model
        # ids / provider URLs from the fallback chain), but send the client only a
        # generic, vendor-neutral message — never the raw error text (prompt.py [12]).
        logger.exception("Chat generation failed", extra={"session_id": session_id})
        yield _sse({"type": "error", "error": "Не удалось получить ответ. Попробуйте ещё раз."})
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

    # Only THIS turn's generated messages (after the last human). Crucial: a
    # terminal tool can leave an empty-content assistant message; falling back
    # over the full rebuilt history would surface the PREVIOUS turn's answer
    # (the "duplicated message" bug).
    generated = split_generated(result.get("messages") or [])
    artifacts = _draft_artifacts(generated)

    assistant_message = result.get("response", "")
    if not assistant_message:
        for message in reversed(generated):
            if isinstance(message, AIMessage):
                content = text_of(message.content).strip()
                if content:
                    assistant_message = content
                    break

    # A drafting turn usually has NO assistant text (the model just calls the
    # tool). Synthesize a short note so the bubble isn't empty / cross-turn.
    if not assistant_message.strip() and artifacts:
        file_name = artifacts[0].get("fileName") or "документ"
        assistant_message = f"Готово — подготовил «{file_name}». Скачать можно по кнопке ниже."

    metadata = result.get("metadata", {}) or {}
    if "toolCallsCount" not in metadata:
        metadata["toolCallsCount"] = result.get("tool_rounds", 0)

    last_user = next((m for m in reversed(payload.messages) if m.role == "user"), None)
    last_user_content = last_user.content if last_user else ""

    if repo and session_id:
        handlers = getattr(request.app.state, "tool_handlers", {}) or {}
        user_row = {
            "role": "user",
            "content": last_user_content,
            "attached_document_ids": unique_document_ids(
                last_user.model_dump() if last_user else None
            ),
        }
        rows = [user_row] + await messages_to_rows(generated, handlers)
        # Persist the synthesized note onto the (empty) drafting assistant row so
        # reload shows it too — not an empty bubble.
        if artifacts:
            for row in rows:
                if (
                    row.get("role") == "assistant"
                    and not (row.get("content") or "").strip()
                    and any(
                        isinstance(c, dict) and c.get("name") == DRAFT_TOOL_NAME
                        for c in (row.get("tool_calls") or [])
                    )
                ):
                    row["content"] = assistant_message
                    break
        await repo.save_messages(session_id, rows)

    logger.info(
        "Chat response sent",
        extra={
            "session_id": session_id,
            "user_id": payload.userId,
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
            "artifacts": artifacts,
        }
    )
