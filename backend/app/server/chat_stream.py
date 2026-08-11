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

The graph is driven by a detached worker task (``_run_turn``) that pushes wire
events into a queue; this generator is only a relay that pumps the queue out to
the client and injects heartbeats. That split is the whole point: when the
browser goes away only the RELAY is cancelled, so the turn still runs to
completion and still persists, and the user finds the answer waiting in the chat
instead of a hole where their question used to be. It also retires the old
``asyncio.shield`` dance — heartbeats no longer share a loop with the model call,
so there is nothing left to shield it from.

Why it used to be inline: before the COI VM migration this file consumed
``graph.astream`` inline, because Yandex Serverless Containers FREEZE/RECLAIM the
instance's CPU the moment the HTTP response ends. A detached task there would be
frozen mid-turn and its LangSmith run would hang forever ("spinner forever"), so
the cancellation HAD to propagate into the model call and the trace had to be
flushed by hand. On a VM nothing freezes and background work simply finishes.
Rolling back to serverless would bring the hanging-trace bug back with it.

SERVERLESS NOTE — the other half of that legacy: serverless also BUFFERED the
whole response body (3.5 MB cap) instead of streaming it out, so the ``token`` /
``status`` events below were correct on the wire but the platform held them until
the turn ended and the client effectively saw only the final answer. On the VM
they reach the browser live (real typewriter + "Ищу практику…" statuses) with
zero changes to this code.
"""
import asyncio
import contextlib
import json
import logging
import time
from typing import AsyncIterator

from fastapi import Request
from langchain_core.messages import AIMessage, ToolMessage

from app.pipelines.messages import messages_to_rows, split_generated, text_of
from app.pipelines.tools.drafting import DRAFT_TOOL_NAME
from app.rag_core.caching import session_affinity_id
from app.server.schema import ChatRequest
from app.services.supabase_repo import SupabaseRepo, unique_document_ids
from app.utils import current_chat_id, current_request_id

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


def _sse(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


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

    # Compare only user-facing rows, mirroring get_messages: tool rows and
    # TEXT-LESS tool-call assistant rows are context-only; a draft turn's note
    # and a preamble with content (streamed live, committed by the client) ARE
    # shown — count them the same way, else this diagnostic logs spurious
    # "history differs" warnings.
    def _is_displayed(row: dict) -> bool:
        if row.get("role") == "user":
            return True
        if row.get("role") != "assistant":
            return False
        calls = row.get("tool_calls") or []
        if not calls:
            return True
        if any(isinstance(c, dict) and c.get("name") == DRAFT_TOOL_NAME for c in calls):
            return True
        return bool((row.get("content") or "").strip())

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


# Strong references to detached turn workers, so the event loop can't garbage
# collect one mid-turn. Nothing more: an earlier version keyed this by chat_id and
# cancelled the previous turn to keep `seq` in chat order, which caused strictly
# worse problems than the ordering it fixed — a second turn arriving in the same
# event-loop batch could cancel a worker before its first step (no sentinel ever
# queued, so that client's SSE stream never terminated), the check-then-register
# straddled an await so two workers could run and persist the same chat twice, and
# a follow-up sent from a second tab silently killed the answer the first tab was
# still streaming. Concurrent turns are now simply allowed: save_messages holds a
# per-session lock and assigns monotonic seq, so the rows stay consistent.
_INFLIGHT_TURNS: set[asyncio.Task] = set()


async def _finalize_turn(
    *,
    result: dict,
    repo: SupabaseRepo | None,
    handlers: dict,
    payload: ChatRequest,
    session_id: str | None,
    project_id: str | None,
    started: float,
) -> dict:
    """Persist the finished turn and build its ``final`` wire payload.

    Deliberately runs inside the turn worker, never in the SSE generator: a user
    who walked away mid-turn must still get the answer saved to their history.
    """
    # Only THIS turn's generated messages (after the last human). Crucial: a
    # terminal tool can leave an empty-content assistant message; falling back
    # over the full rebuilt history would surface the PREVIOUS turn's answer
    # (the "duplicated message" bug).
    generated = split_generated(result.get("messages") or [])
    artifacts = _draft_artifacts(generated)

    assistant_message = result.get("response", "")
    if not assistant_message:
        # Fall back only to trailing AI text WITHOUT tool calls: text on a
        # tool-call message is the streamed preamble — the client already
        # committed it live on the status event (and get_messages shows it on
        # reload), so carrying it in `final` would display it twice.
        for message in reversed(generated):
            if isinstance(message, AIMessage):
                if getattr(message, "tool_calls", None):
                    continue
                content = text_of(message.content).strip()
                if content:
                    assistant_message = content
                    break

    # A drafting turn usually has NO assistant text (the model just calls the
    # tool). Synthesize a short note so the bubble isn't empty / cross-turn.
    # The note must match the artifact status: a failed draft next to a
    # "Готово — подготовил" bubble reads as a broken product.
    if not assistant_message.strip() and artifacts:
        file_name = artifacts[0].get("fileName") or "документ"
        if artifacts[0].get("status") == "ready":
            assistant_message = f"Готово — подготовил «{file_name}». Скачать можно по кнопке ниже."
        else:
            assistant_message = (
                "Не удалось подготовить документ. Попробуйте ещё раз — "
                "при необходимости уточните, какой документ нужен."
            )

    metadata = result.get("metadata", {}) or {}
    if "toolCallsCount" not in metadata:
        metadata["toolCallsCount"] = result.get("tool_rounds", 0)

    last_user = next((m for m in reversed(payload.messages) if m.role == "user"), None)
    last_user_content = last_user.content if last_user else ""

    if repo and session_id:
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

    return {
        "type": "final",
        "message": assistant_message,
        "sessionId": session_id,
        "projectId": project_id,
        "metadata": metadata,
        "artifacts": artifacts,
    }


async def _run_turn(
    *,
    agen,
    queue: asyncio.Queue,
    progress: dict,
    repo: SupabaseRepo | None,
    handlers: dict,
    payload: ChatRequest,
    session_id: str | None,
    project_id: str | None,
    started: float,
) -> None:
    """Drive the graph to completion, persist, and queue the wire events.

    Detached from the request on purpose: a client disconnect cancels the SSE
    relay, not this task, so the turn finishes and is saved rather than being
    paid for and thrown away. Queue items are ``(kind, chunk)`` — the relay needs
    ``kind`` to count what it actually sent and to know when to stop.
    """
    result: dict | None = None
    announced_tool_calls: set[str] = set()

    def emit(kind: str, chunk: bytes) -> None:
        # Once the client is gone nobody drains the queue — keep finishing the
        # turn (that is the whole point) but stop buffering wire events for an
        # empty room, or a long answer piles up megabytes of dead tuples.
        if not progress.get("detached"):
            queue.put_nowait((kind, chunk))

    try:
        # stream_mode=["messages", "values"]:
        #   - "messages" yields (AIMessageChunk, metadata) for token deltas;
        #   - "values" yields the full state after each node — used to (a) announce
        #     a tool BEFORE it runs (the generate snapshot carries the tool_calls)
        #     and (b) capture the final state (== what graph.ainvoke would return).
        async for mode, data in agen:
            if mode == "messages":
                chunk, meta = data
                if meta.get("langgraph_node") == "generate":
                    delta = _token_delta(chunk)
                    if delta:
                        emit("token", _sse({"type": "token", "delta": delta}))
            elif mode == "values":
                result = data
                progress["tool_rounds"] = data.get("tool_rounds", 0)
                messages = data.get("messages") or []
                if messages:
                    for call in getattr(messages[-1], "tool_calls", None) or []:
                        key = call.get("id") or f"{call.get('name')}:{len(announced_tool_calls)}"
                        if key in announced_tool_calls:
                            continue
                        announced_tool_calls.add(key)
                        name = call.get("name", "")
                        # last_tool stays server-side for the cancel diagnostic;
                        # only the human-readable label goes on the wire. The raw
                        # internal tool name (search_court_practice / draft_document…)
                        # would disclose the system's machinery (prompt.py [12]).
                        progress["last_tool"] = name
                        emit(
                            "status",
                            _sse(
                                {
                                    "type": "status",
                                    "label": TOOL_STATUS_LABELS.get(
                                        name, DEFAULT_TOOL_STATUS
                                    ),
                                }
                            ),
                        )

        if result is not None:
            final = await _finalize_turn(
                result=result,
                repo=repo,
                handlers=handlers,
                payload=payload,
                session_id=session_id,
                project_id=project_id,
                started=started,
            )
            emit("final", _sse(final))
    except asyncio.CancelledError:
        # A client disconnect cancels the relay, not this task, so in practice this
        # only happens when the process is shutting down mid-deploy — the turn is
        # lost, which is worth a log line since the user was waiting for it.
        # (LangSmith events of the cancelled run are flushed by the lifespan
        # shutdown hook in main.py right after this.)
        logger.info(
            "Chat turn cancelled",
            extra={"session_id": session_id, "elapsed_s": round(time.time() - started, 1)},
        )
        # Keep the wire contract intact: every stream ends with `final` or `error`,
        # never in silence. `emit` is a no-op once the client is gone.
        emit("error", _sse({"type": "error", "error": "Запрос отменён."}))
        raise
    except Exception:  # noqa: BLE001 - surface any generation failure to the client
        # Log the full exception server-side (str(error) can embed upstream model
        # ids / provider URLs from the fallback chain), but send the client only a
        # generic, vendor-neutral message — never the raw error text (prompt.py [12]).
        logger.exception("Chat generation failed", extra={"session_id": session_id})
        emit(
            "error",
            _sse({"type": "error", "error": "Не удалось получить ответ. Попробуйте ещё раз."}),
        )
    finally:
        # The relay also gets a "done" from this task's done-callback, so it can
        # terminate even if this body never ran (a task cancelled before its first
        # step skips try/finally entirely). A duplicate sentinel is harmless: the
        # relay breaks on the first one.
        queue.put_nowait(("done", b""))
        with contextlib.suppress(Exception):
            await agen.aclose()


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

    # Pin OpenRouter provider routing to this conversation so prompt-cache
    # writes and reads land on the same provider (see rag_core/caching.py).
    # Set before the graph runs: every LLM task of the turn inherits it.
    session_affinity_id.set(chat_id)

    graph, state, config = await _prepare_graph_run(request, payload, chat_id, project_id, history)

    agen = graph.astream(state, config=config, stream_mode=["messages", "values"])
    queue: asyncio.Queue = asyncio.Queue()
    # Written by the worker, read by the cancel diagnostic below.
    progress: dict = {"last_tool": None, "tool_rounds": 0}
    task = asyncio.create_task(
        _run_turn(
            agen=agen,
            queue=queue,
            progress=progress,
            repo=repo,
            handlers=getattr(request.app.state, "tool_handlers", {}) or {},
            payload=payload,
            session_id=session_id,
            project_id=project_id,
            started=started,
        )
    )
    _INFLIGHT_TURNS.add(task)
    # Belt and braces on the relay's exit: a task cancelled before its first step
    # skips its own try/finally, so the sentinel has to come from the task object
    # rather than from the coroutine body. Without this the relay's only exit is a
    # sentinel that would never arrive and it heartbeats until the client gives up.
    def _turn_done(finished: asyncio.Task) -> None:
        _INFLIGHT_TURNS.discard(finished)
        queue.put_nowait(("done", b""))

    task.add_done_callback(_turn_done)

    tokens_sent = 0  # how far the answer got — used to make a 499/cancel diagnosable
    completed = False
    # Stamped explicitly rather than left to the contextvars: the block below can
    # end up running at garbage-collection time inside an unrelated request's task
    # (see its comment), where the ambient chat_id/request_id belong to somebody
    # else's chat and would misattribute this disconnect.
    log_context = {"chat_id": current_chat_id(), "request_id": current_request_id()}
    last_activity = started  # when the client last took something from us
    try:
        while True:
            try:
                kind, chunk = await asyncio.wait_for(
                    queue.get(), timeout=HEARTBEAT_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                # Still working (e.g. a slow OpenSearch tool); keep the connection
                # warm. Nothing to shield: the model call lives in `task`, which
                # this timeout cannot touch.
                yield b": heartbeat\n\n"
                last_activity = time.time()
                continue

            if kind == "done":
                completed = True
                break
            if kind == "token":
                tokens_sent += 1
            yield chunk
            last_activity = time.time()
    finally:
        # Anything other than a clean "done" means the client went away mid-turn.
        # This has to be a `finally`, not `except CancelledError`: how Starlette
        # tears us down depends on the ASGI spec_version it negotiated — on <2.4 it
        # cancels the task running the response (CancelledError arrives here, and
        # that is what prod does today), on >=2.4 `send` raises OSError and we are
        # closed with GeneratorExit at the `yield` instead. A cancel can also land
        # on `send` rather than on our own await. `finally` covers every variant;
        # no `await` in here, so it is safe under GeneratorExit.
        #
        # `task` is deliberately NOT cancelled: it finishes the turn and persists
        # it, so the answer is waiting in the chat on the user's next visit. The
        # flag tells the worker to stop queueing events nobody will ever read.
        #
        # Timing caveat: when the server is torn down while this generator sits at
        # a `yield` (the ASGI app can simply return and drop it), Python finalizes
        # it later, at GC, inside whatever task happened to trigger the collection.
        # So treat this line as "the client stopped reading", not "it stopped at
        # this instant" — hence elapsed_s is measured to the last drained event
        # rather than to now, and the ids are stamped from `log_context`.
        if not completed:
            progress["detached"] = True
            # Make the 499/cancel diagnosable: how far the answer got and where the
            # turn was when the client vanished. Big elapsed_s with tokens_sent>0 ⇒
            # a long turn likely dropped by an idle timeout; near-zero elapsed_s
            # with tokens_sent==0 ⇒ the user gave up staring at a spinner.
            phase = (
                "streaming_answer" if tokens_sent
                else f"tool:{progress['last_tool']}" if progress["last_tool"]
                else "thinking"
            )
            logger.info(
                "Chat stream cancelled (client disconnected)",
                extra={
                    **log_context,
                    "session_id": session_id,
                    "elapsed_s": round(last_activity - started, 1),
                    "phase": phase,
                    "tokens_sent": tokens_sent,
                    "tool_rounds": progress["tool_rounds"],
                },
            )
