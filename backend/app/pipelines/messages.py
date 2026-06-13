"""Two-way bridge between persisted chat rows and LangChain messages.

* :func:`rows_to_messages` — DB rows → ``[System, Human, AI(tool_calls), Tool, …]``
  for the model's context. Tool rows are rehydrated through their handler.
* :func:`messages_to_rows` — a turn's freshly generated messages → DB rows.
  Tool results are passed through their handler's ``capture`` so heavy payloads
  (e.g. full act text) are not stored.

Both are async because handlers may do I/O (fetch a document, call an API).
"""
import logging
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.pipelines.tools.base import ToolResultHandler
from app.rag_core.prompt import get_system_prompt

logger = logging.getLogger(__name__)

MAX_CONTEXT_DOCUMENTS = 20
MISSING_TOOL_RESULT = "[Результат инструмента недоступен]"


def text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        return "".join(parts)
    return str(content or "")


def _format_with_attachments(row: dict, documents_by_id: dict[str, dict]) -> str:
    content = row.get("content") or ""
    attachment_ids = [
        doc_id
        for doc_id in (row.get("attachedDocumentIds") or [])
        if isinstance(doc_id, str) and doc_id.strip()
    ][:MAX_CONTEXT_DOCUMENTS]

    prepared = []
    for doc_id in attachment_ids:
        doc = documents_by_id.get(doc_id)
        if doc and doc.get("text"):
            name = doc.get("name") or "Документ"
            prepared.append(f"Документ: {name}\n\n{doc['text']}")

    if not prepared:
        return content

    block = "[Прикрепленные документы к этому сообщению]\n\n" + "\n\n---\n\n".join(prepared)
    return f"{content}\n\n{block}" if content.strip() else block


def _serialize_tool_calls(tool_calls: list[dict] | None) -> list[dict] | None:
    if not tool_calls:
        return None
    serialized = []
    for call in tool_calls:
        serialized.append(
            {
                "id": call.get("id"),
                "name": call.get("name"),
                "args": call.get("args") or {},
            }
        )
    return serialized


async def rows_to_messages(
    rows: list[dict],
    documents_by_id: dict[str, dict],
    handlers: dict[str, ToolResultHandler],
) -> list[BaseMessage]:
    """Rebuild the model's message list from persisted rows (system prompt first).

    Defensive about the OpenAI/OpenRouter contract: a tool_call is only replayed
    if a matching tool row exists, and a tool row is only replayed if its call
    was replayed. This keeps every assistant ``tool_call`` adjacent to its tool
    result even if the stored log is malformed (e.g. an orphaned call left by a
    round-limit cutoff).
    """
    answered_ids = {row.get("tool_call_id") for row in rows if row.get("role") == "tool"}

    messages: list[BaseMessage] = [SystemMessage(content=get_system_prompt())]
    pending_args: dict[str, dict] = {}  # tool_call_id -> original args (only for replayed calls)

    for row in rows:
        role = row.get("role")
        if role == "tool":
            tool_call_id = row.get("tool_call_id") or ""
            if tool_call_id not in pending_args:
                continue  # orphan tool row (no preceding replayed call) — skip
            name = row.get("tool_name") or ""
            state = row.get("tool_state") or {}
            handler = handlers.get(name)
            if handler is not None:
                content = await handler.run(args=pending_args[tool_call_id], state=state)
            else:
                content = state.get("content") or ""
                if not content:
                    logger.warning("No handler for stored tool '%s'; replaying placeholder", name)
                    content = MISSING_TOOL_RESULT
            messages.append(ToolMessage(content=content, tool_call_id=tool_call_id, name=name))
        elif role == "assistant":
            content = row.get("content") or ""
            valid_calls = [
                call for call in (row.get("tool_calls") or []) if call.get("id") in answered_ids
            ]
            if valid_calls:
                messages.append(AIMessage(content=content, tool_calls=valid_calls))
                for call in valid_calls:
                    pending_args[call["id"]] = call.get("args") or {}
            elif content.strip():
                # No replayable calls: drop dangling tool_calls, keep any text.
                messages.append(AIMessage(content=content))
        else:  # user (and any unknown role) treated as human input
            messages.append(HumanMessage(content=_format_with_attachments(row, documents_by_id)))

    return messages


def split_generated(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Messages produced this turn = everything after the last human message."""
    last_human = -1
    for index, message in enumerate(messages):
        if isinstance(message, HumanMessage):
            last_human = index
    return messages[last_human + 1 :] if last_human >= 0 else []


async def messages_to_rows(
    generated: list[BaseMessage],
    handlers: dict[str, ToolResultHandler],
) -> list[dict]:
    """Serialize a turn's generated AI/Tool messages into persistable rows.

    Never persists an unanswered ``tool_call``: if the loop ended on an
    assistant message whose calls have no tool result (e.g. a round-limit
    cutoff), those calls are dropped, and an assistant row with neither text
    nor calls is omitted entirely. This keeps the stored log replayable.
    """
    rows: list[dict] = []
    pending: dict[str, dict] = {}  # tool_call_id -> {"name", "args"}

    for message in generated:
        if isinstance(message, AIMessage):
            tool_calls = _serialize_tool_calls(getattr(message, "tool_calls", None))
            rows.append(
                {
                    "role": "assistant",
                    "content": text_of(message.content),
                    "tool_calls": tool_calls,
                }
            )
            for call in tool_calls or []:
                if call.get("id"):
                    pending[call["id"]] = {"name": call.get("name"), "args": call.get("args") or {}}
        elif isinstance(message, ToolMessage):
            call = pending.get(message.tool_call_id, {})
            name = call.get("name") or getattr(message, "name", "") or ""
            args = call.get("args") or {}
            handler = handlers.get(name)
            if handler is not None:
                state = await handler.capture(args=args, content=text_of(message.content))
            else:
                state = {"content": text_of(message.content)}
            rows.append(
                {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "tool_name": name,
                    "tool_state": state,
                }
            )

    # Drop tool_calls that no tool row answered; omit now-empty assistant rows.
    answered = {row["tool_call_id"] for row in rows if row["role"] == "tool"}
    cleaned: list[dict] = []
    for row in rows:
        if row["role"] == "assistant" and row.get("tool_calls"):
            kept = [call for call in row["tool_calls"] if call.get("id") in answered]
            row = {**row, "tool_calls": kept or None}
        if row["role"] == "assistant" and not (row.get("content") or "").strip() and not row.get("tool_calls"):
            continue
        cleaned.append(row)
    return cleaned
