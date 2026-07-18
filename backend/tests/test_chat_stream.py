"""Tests for the SSE translation in stream_chat.

These drive stream_chat with a fake graph whose ``astream`` yields the same
(mode, data) tuples LangGraph emits for stream_mode=["messages", "values"], and
assert the wire events (token deltas, tool status, final, error) are correct —
without needing a real LLM or DB (repo is None, so no persistence runs).
"""
import json
import types

from langchain_core.messages import AIMessage, AIMessageChunk

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


def _request(graph):
    app = types.SimpleNamespace(
        state=types.SimpleNamespace(repo=None, chat_graph=graph, tool_handlers={})
    )
    return types.SimpleNamespace(app=app)


def _payload():
    return ChatRequest(messages=[{"role": "user", "content": "вопрос"}], projectId="p1")


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
