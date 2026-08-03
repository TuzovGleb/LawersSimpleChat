"""Tests for the SSE translation in stream_chat.

These drive stream_chat with a fake graph whose ``astream`` yields the same
(mode, data) tuples LangGraph emits for stream_mode=["messages", "values"], and
assert the wire events (token deltas, tool status, final, error) are correct —
without needing a real LLM or DB (repo is None, so no persistence runs).

The second half covers the abandoned-turn contract: the graph runs in a detached
worker, so a client that disconnects mid-turn still gets its answer persisted,
and a new turn in the same chat cancels the abandoned one first.
"""
import asyncio
import contextlib
import json
import logging
import types

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from app.pipelines.tools.drafting import DRAFT_TOOL_NAME
from app.server import chat_stream
from app.server.chat_stream import stream_chat
from app.server.schema import ChatRequest


class FakeGraph:
    def __init__(self, events):
        self._events = events

    def astream(self, state, config=None, stream_mode=None):
        async def gen():
            for event in self._events:
                yield event

        return gen()


class FakeExplodingGraph:
    def astream(self, state, config=None, stream_mode=None):
        async def gen():
            raise RuntimeError("boom")
            yield  # pragma: no cover - makes gen an async generator

        return gen()


class SlowGraph:
    """Streams one token, then blocks on ``release`` before finishing the turn.

    Lets a test abandon a turn while it is genuinely in flight and then let it
    run to completion, which is exactly the disconnect scenario.
    """

    def __init__(self, release: asyncio.Event, tail):
        self._release = release
        self._tail = tail

    def astream(self, state, config=None, stream_mode=None):
        async def gen():
            yield (
                "messages",
                (AIMessageChunk(content="Начало"), {"langgraph_node": "generate"}),
            )
            await self._release.wait()
            for event in self._tail:
                yield event

        return gen()


class FakeRepo:
    """Minimal SupabaseRepo stand-in that records what a turn persisted."""

    def __init__(self, save_ok: bool = True):
        self.saved: list[tuple[str, list[dict]]] = []
        self.save_ok = save_ok

    async def session_exists(self, session_id):
        return True

    async def is_foreign_session(self, session_id, user_id):
        return False

    async def load_history(self, session_id):
        return []

    async def load_attached_documents(self, project_id, history):
        return {}

    async def save_messages(self, session_id, rows):
        self.saved.append((session_id, rows))
        # Реальный репозиторий возвращает признак успеха, и _finalize_turn на
        # него смотрит: двойник обязан отвечать так же, иначе тесты незаметно
        # уезжают в ветку «ход не сохранён».
        return self.save_ok


# Mirrors the real graph state: `messages` is the whole conversation, so the
# HumanMessage has to be there — split_generated slices the turn off after it.
_FINAL_VALUES = (
    "values",
    {
        "messages": [HumanMessage(content="вопрос"), AIMessage(content="Готовый ответ")],
        "response": "Готовый ответ",
        "metadata": {"modelUsed": "anthropic/claude"},
        "tool_rounds": 0,
    },
)


def _request(graph, repo=None):
    app = types.SimpleNamespace(
        state=types.SimpleNamespace(repo=repo, chat_graph=graph, tool_handlers={})
    )
    return types.SimpleNamespace(app=app)


def _payload():
    return ChatRequest(messages=[{"role": "user", "content": "вопрос"}], projectId="p1")


@contextlib.contextmanager
def _capture_logs(logger_name: str = "app.server.chat_stream"):
    """Collect records straight off the logger, level included.

    Not pytest's ``caplog``, and not just an added handler — this has to be immune
    to whether app.config has already applied logging.yaml, which differs between
    a single-file run and the full suite:
      * with the config applied, the ``app`` logger has ``propagate: false``, so
        caplog's root-attached handler never sees the record;
      * without it, the effective level is root's WARNING, so ``logger.info``
        returns before reaching any handler at all.
    Attaching to the emitting logger AND pinning its level covers both.
    """
    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger(logger_name)
    handler = _Collect()
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


async def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.005)


async def _new_worker(before: set) -> asyncio.Task:
    """The turn worker a stream just spawned (the registry is a plain set)."""
    await _wait_until(lambda: bool(chat_stream._INFLIGHT_TURNS - before))
    return next(iter(chat_stream._INFLIGHT_TURNS - before))


async def _collect(agen) -> str:
    chunks = [chunk async for chunk in agen]
    return b"".join(chunks).decode("utf-8")


def _data_events(text: str) -> list[dict]:
    events = []
    for line in text.split("\n"):
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: ") :]))
    return events


async def test_stream_emits_tokens_status_and_final():
    events = [
        ("messages", (AIMessageChunk(content="Соглас"), {"langgraph_node": "generate"})),
        ("messages", (AIMessageChunk(content="но"), {"langgraph_node": "generate"})),
        (
            "values",
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "search_court_practice",
                                "args": {},
                                "id": "t1",
                                "type": "tool_call",
                            }
                        ],
                    )
                ]
            },
        ),
        ("messages", (AIMessageChunk(content=" практике"), {"langgraph_node": "generate"})),
        (
            "values",
            {
                "messages": [AIMessage(content="Согласно практике ВС РФ…")],
                "response": "Согласно практике ВС РФ…",
                "metadata": {"modelUsed": "anthropic/claude"},
                "tool_rounds": 1,
            },
        ),
    ]

    text = await _collect(stream_chat(_request(FakeGraph(events)), "chat-1", _payload()))
    parsed = _data_events(text)
    by_type: dict[str, list[dict]] = {}
    for event in parsed:
        by_type.setdefault(event.get("type"), []).append(event)

    # Token deltas stream through, in order.
    assert [e["delta"] for e in by_type["token"]] == ["Соглас", "но", " практике"]

    # Tool start is announced once, with the human label. The raw internal
    # tool name must NOT be on the wire (prompt-extraction hardening; see the
    # wire-contract note at the top of chat_stream.py).
    assert len(by_type["status"]) == 1
    assert "tool" not in by_type["status"][0]
    assert by_type["status"][0]["label"] == "Ищу судебную практику…"

    # Exactly one final event carrying the answer + metadata (+ legacy `message`).
    assert len(by_type["final"]) == 1
    final = by_type["final"][0]
    assert final["message"] == "Согласно практике ВС РФ…"
    assert final["metadata"]["modelUsed"] == "anthropic/claude"
    assert final["metadata"]["toolCallsCount"] == 1


async def test_unknown_tool_falls_back_to_default_label():
    events = [
        (
            "values",
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {"name": "some_new_tool", "args": {}, "id": "x", "type": "tool_call"}
                        ],
                    )
                ]
            },
        ),
        (
            "values",
            {"messages": [AIMessage(content="ответ")], "response": "ответ", "metadata": {}},
        ),
    ]
    text = await _collect(stream_chat(_request(FakeGraph(events)), "chat-2", _payload()))
    status = [e for e in _data_events(text) if e.get("type") == "status"]
    assert status and status[0]["label"] == "Работаю с источниками…"


async def test_generation_failure_emits_error_event():
    text = await _collect(stream_chat(_request(FakeExplodingGraph()), "chat-3", _payload()))
    events = _data_events(text)
    assert any(e.get("type") == "error" for e in events)
    assert not any(e.get("type") == "final" for e in events)


async def test_client_disconnect_still_persists_the_turn():
    """The regression this whole design exists for.

    Before the worker split, a disconnect skipped the persist block entirely: the
    turn ran to completion in an orphaned task and the answer — plus the user's
    own question — was thrown away.
    """
    release = asyncio.Event()
    repo = FakeRepo()
    request = _request(SlowGraph(release, [_FINAL_VALUES]), repo=repo)

    seen: list[bytes] = []

    async def consume():
        async for chunk in stream_chat(request, "chat-abandoned", _payload()):
            seen.append(chunk)

    before = set(chat_stream._INFLIGHT_TURNS)
    consumer = asyncio.create_task(consume())
    worker = await _new_worker(before)
    await _wait_until(lambda: any(b'"type": "token"' in chunk for chunk in seen))

    # The browser goes away: Starlette cancels the task running the generator.
    consumer.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await consumer

    assert not worker.done(), "the turn must survive the client leaving"

    release.set()
    await asyncio.wait({worker}, timeout=5)

    assert worker.done() and not worker.cancelled()
    assert [session for session, _ in repo.saved] == ["chat-abandoned"]
    rows = repo.saved[0][1]
    assert rows[0]["role"] == "user" and rows[0]["content"] == "вопрос"
    assert any(r["role"] == "assistant" and r["content"] == "Готовый ответ" for r in rows)


async def test_generator_close_also_detaches_and_persists():
    """The other teardown path: Starlette closes the response iterator instead of
    cancelling the task (ASGI spec_version >= 2.4, or a cancel landing on `send`),
    so the relay sees GeneratorExit at its `yield`, never CancelledError.
    """
    release = asyncio.Event()
    repo = FakeRepo()
    request = _request(SlowGraph(release, [_FINAL_VALUES]), repo=repo)

    before = set(chat_stream._INFLIGHT_TURNS)
    stream = stream_chat(request, "chat-closed", _payload())
    await stream.__anext__()  # up-front heartbeat
    await stream.__anext__()  # first token: the turn is genuinely in flight
    worker = await _new_worker(before)

    with _capture_logs() as records:
        await stream.aclose()

    # The diagnostic must survive this path too — it is how a disconnect is found
    # in Cloud Logging at all.
    assert any("Chat stream cancelled" in record.getMessage() for record in records)

    assert not worker.done()
    release.set()
    await asyncio.wait({worker}, timeout=5)
    assert [session for session, _ in repo.saved] == ["chat-closed"]


async def test_concurrent_turns_in_one_chat_both_finish_and_persist():
    """Two tabs on one chat must not fight.

    An earlier version cancelled the older turn to keep `seq` in chat order. That
    threw away an answer the first tab was still streaming, and the check-then-
    register straddled an await so both turns could end up registered anyway and
    save twice. Concurrency is now simply allowed — save_messages serializes the
    rows under a per-session lock.
    """
    repo = FakeRepo()
    first = _request(FakeGraph([_FINAL_VALUES]), repo=repo)
    second = _request(FakeGraph([_FINAL_VALUES]), repo=repo)

    texts = await asyncio.gather(
        _collect(stream_chat(first, "chat-parallel", _payload())),
        _collect(stream_chat(second, "chat-parallel", _payload())),
    )

    for text in texts:
        assert any(e.get("type") == "final" for e in _data_events(text))
    assert len(repo.saved) == 2


async def test_relay_terminates_when_its_worker_is_cancelled():
    """The relay's only loop exit is the worker's sentinel, so the sentinel must
    not depend on the worker's body running: a task cancelled before its first
    step skips its own try/finally entirely, and the client would then be fed
    heartbeats forever with no `final` and no `error`. The done-callback covers it.
    """
    release = asyncio.Event()  # never set: the worker is killed, not completed
    before = set(chat_stream._INFLIGHT_TURNS)
    request = _request(SlowGraph(release, [_FINAL_VALUES]))

    consumer = asyncio.create_task(
        _collect(stream_chat(request, "chat-killed", _payload()))
    )
    worker = await _new_worker(before)
    worker.cancel()

    text = await asyncio.wait_for(consumer, timeout=5)
    assert any(e.get("type") == "error" for e in _data_events(text))


async def test_heartbeat_keeps_the_connection_warm_while_the_graph_works(monkeypatch):
    monkeypatch.setattr(chat_stream, "HEARTBEAT_INTERVAL_SECONDS", 0.01)
    release = asyncio.Event()

    async def release_soon():
        await asyncio.sleep(0.05)
        release.set()

    releaser = asyncio.create_task(release_soon())
    text = await _collect(
        stream_chat(_request(SlowGraph(release, [_FINAL_VALUES])), "chat-hb", _payload())
    )
    await releaser

    # More than the single up-front heartbeat: the relay kept pinging while the
    # graph was blocked, which is what stops proxies from closing the stream.
    assert text.count(": heartbeat") >= 2
    assert any(e.get("type") == "final" for e in _data_events(text))


def _drafting_final(*, file_name: str = "Исковое заявление"):
    """Ход, в котором модель вызвала draft_document и получила готовый документ."""
    call = {"name": DRAFT_TOOL_NAME, "id": "call-draft-1", "args": {}}
    return (
        "values",
        {
            "messages": [
                HumanMessage(content="подготовь иск"),
                AIMessage(content="", tool_calls=[call]),
                ToolMessage(
                    content=json.dumps({"status": "ready", "file_name": file_name}),
                    tool_call_id="call-draft-1",
                ),
            ],
            "response": "",
            "metadata": {"modelUsed": "anthropic/claude"},
            "tool_rounds": 1,
        },
    )


def _final_event(text: str) -> dict:
    return next(e for e in _data_events(text) if e.get("type") == "final")


async def test_saved_drafting_turn_offers_the_document():
    repo = FakeRepo()
    request = _request(FakeGraph([_drafting_final()]), repo=repo)

    final = _final_event(await _collect(stream_chat(request, "chat-draft-ok", _payload())))

    assert [a["status"] for a in final["artifacts"]] == ["ready"]
    assert "не сохранил" not in final["message"]


async def test_lost_turn_does_not_promise_a_downloadable_document():
    """Гвоздь инцидента: ход не записался, а клиент получал artifact "ready".

    Кнопка вела на render-on-demand, который читает tool_state из базы, — там
    пусто, и скачивание отдавало 404 навсегда. Раз ход потерян, обещать файл
    нельзя, и молчать об исчезающей переписке тоже.
    """
    repo = FakeRepo(save_ok=False)
    request = _request(FakeGraph([_drafting_final()]), repo=repo)

    final = _final_event(await _collect(stream_chat(request, "chat-draft-lost", _payload())))

    assert [a["status"] for a in final["artifacts"]] == ["unsaved"]
    assert "не сохранил" in final["message"]
