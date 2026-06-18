"""Chat graph nodes: context assembly and generation."""
import logging
import time

from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool

from app.pipelines.messages import rows_to_messages, text_of
from app.pipelines.tools.base import ToolResultHandler
from app.rag_core.llm import ChatModelRegistry

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 4


async def build_context(state: dict, *, handlers: dict[str, ToolResultHandler] | None = None) -> dict:
    """Rebuild the model's message list from history (tool rows rehydrated)."""
    history = state.get("history") or []
    documents_by_id = state.get("documents_by_id") or {}

    messages = await rows_to_messages(history, documents_by_id, handlers or {})

    logger.info(
        "Context assembled",
        extra={
            "history_len": len(history),
            "attached_documents": len(documents_by_id),
            "selected_model": state.get("selected_model"),
        },
    )
    return {"messages": messages, "tool_rounds": 0}


def _extract_metadata(
    model_used: str,
    response: AIMessage,
    response_time_ms: int,
    *,
    tool_rounds: int = 0,
) -> dict:
    usage = getattr(response, "usage_metadata", None) or {}
    finish_reason = (response.response_metadata or {}).get("finish_reason", "stop")
    return {
        "modelUsed": model_used,
        "fallbackOccurred": False,
        "chunksCount": 1,
        "totalTokens": usage.get("total_tokens", 0),
        "finishReason": finish_reason or "stop",
        "responseTimeMs": response_time_ms,
        "provider": "openrouter",
        "toolCallsCount": tool_rounds,
    }


def route_after_generate(state: dict) -> str:
    messages = state.get("messages") or []
    if not messages:
        return "end"

    last_message = messages[-1]
    tool_calls = getattr(last_message, "tool_calls", None)
    if tool_calls:
        rounds = state.get("tool_rounds", 0)
        if rounds >= MAX_TOOL_ROUNDS:
            logger.warning("Tool round limit reached", extra={"tool_rounds": rounds})
            return "end"
        return "tools"
    return "end"


async def increment_tool_rounds(state: dict) -> dict:
    return {"tool_rounds": state.get("tool_rounds", 0) + 1}


async def generate(state: dict, *, registry: ChatModelRegistry, tools: list[BaseTool] | None = None) -> dict:
    """Call the selected OpenRouter model, with tools bound while rounds remain."""
    model_used, llm = registry.resolve(state.get("selected_model"))
    tool_rounds = state.get("tool_rounds", 0)
    llm_to_invoke = llm.bind_tools(tools) if tools and tool_rounds < MAX_TOOL_ROUNDS else llm
    started = time.time()

    response = await llm_to_invoke.ainvoke(state["messages"])
    response_time_ms = int((time.time() - started) * 1000)

    tool_calls = getattr(response, "tool_calls", None) or []
    content = text_of(response.content)

    if not tool_calls and (not content or not content.strip()):
        raise RuntimeError("Empty response from model")

    metadata = _extract_metadata(model_used, response, response_time_ms, tool_rounds=tool_rounds)
    logger.info(
        "Generation complete",
        extra={
            "model_used": model_used,
            "response_chars": len(content),
            "tool_calls": len(tool_calls),
            "total_tokens": metadata["totalTokens"],
            "finish_reason": metadata["finishReason"],
            "response_time_ms": response_time_ms,
        },
    )

    result: dict = {"messages": [response], "metadata": metadata}
    if not tool_calls:
        result["response"] = content
    return result
