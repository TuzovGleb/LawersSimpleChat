"""Chat graph nodes: context assembly and generation."""
import logging
import time

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from openai import APIError

from app.pipelines.messages import rows_to_messages, text_of
from app.pipelines.tools.base import ToolResultHandler
from app.rag_core.llm import ChatModelRegistry, ChatOpenAI

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8

# Number of extra attempts on the *same* model when it returns an empty
# response (no content, no tool calls) before moving to the next model in the
# fallback chain. Empty responses are often transient (e.g. a reasoning model
# that returned only reasoning tokens), so a cheap re-ask frequently succeeds.
EMPTY_RESPONSE_RETRIES = 1


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
    # NB: ``modelUsed`` here is the internal registry key. After the chat.yaml
    # rename these keys are deliberately vendor-neutral (fast/thinking/power/alt),
    # so nothing sent to the client discloses the underlying model or vendor. Do
    # NOT reintroduce a raw provider/model id or a "provider" field here — the SSE
    # ``final`` event forwards this dict to the browser verbatim.
    return {
        "modelUsed": model_used,
        "fallbackOccurred": False,
        "chunksCount": 1,
        "totalTokens": usage.get("total_tokens", 0),
        "finishReason": finish_reason or "stop",
        "responseTimeMs": response_time_ms,
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


def route_after_tools(state: dict, *, terminal_names: frozenset[str]) -> str:
    """After the tool node: END if a terminal tool ran, else loop to generate.

    Terminal-ness is a hardcoded tool property (see ``ToolSpec.terminal``), not a
    model decision. We inspect the trailing run of ``ToolMessage``s appended this
    round (one per tool call) and end the turn if any came from a terminal tool.
    """
    if not terminal_names:
        return "generate"
    for message in reversed(state.get("messages") or []):
        if isinstance(message, ToolMessage):
            if getattr(message, "name", None) in terminal_names:
                return "end"
            continue
        break  # reached the AIMessage that issued the calls; stop scanning
    return "generate"


def _is_empty(response: AIMessage, tool_calls: list) -> bool:
    """A response is empty when it has neither tool calls nor any text content."""
    return not tool_calls and not text_of(response.content).strip()


async def _invoke_with_fallback(
    chain: list[tuple[str, ChatOpenAI]],
    messages: list,
    tools: list[BaseTool] | None,
    tool_rounds: int,
) -> tuple[str, AIMessage, list, int, bool]:
    """Try each model in ``chain`` until one returns a usable response.

    Mirrors the recognizer fallback (``recognizers/fallback.py``): a model that
    raises a transient API error, or returns an empty response, is logged and
    the next model is tried. An empty response triggers up to
    ``EMPTY_RESPONSE_RETRIES`` re-asks of the *same* model first. If every model
    fails, the accumulated reasons are raised as a ``RuntimeError``.

    Returns ``(model_used, response, tool_calls, response_time_ms, fallback_occurred)``.
    """
    bind = bool(tools) and tool_rounds < MAX_TOOL_ROUNDS
    reasons: list[str] = []

    for index, (name, llm) in enumerate(chain):
        llm_to_invoke = llm.bind_tools(tools) if bind else llm
        for attempt in range(1 + EMPTY_RESPONSE_RETRIES):
            try:
                started = time.time()
                response = await llm_to_invoke.ainvoke(messages)
                response_time_ms = int((time.time() - started) * 1000)
            except APIError as err:
                logger.warning(
                    "Model call failed; trying next in fallback chain",
                    extra={"model": name, "error_type": type(err).__name__},
                    exc_info=True,
                )
                reasons.append(f"{name}: {type(err).__name__}: {err}")
                break  # don't retry the same model on a hard API error

            tool_calls = getattr(response, "tool_calls", None) or []
            if not _is_empty(response, tool_calls):
                return name, response, tool_calls, response_time_ms, index > 0

            if attempt < EMPTY_RESPONSE_RETRIES:
                logger.warning(
                    "Empty response; retrying same model",
                    extra={"model": name, "attempt": attempt + 1},
                )
                continue

            logger.warning(
                "Empty response; trying next in fallback chain",
                extra={"model": name},
            )
            reasons.append(f"{name}: empty response")

    raise RuntimeError("All models failed: " + "; ".join(reasons))


async def generate(state: dict, *, registry: ChatModelRegistry, tools: list[BaseTool] | None = None) -> dict:
    """Call the selected OpenRouter model, falling back across the configured chain.

    Tools are bound while tool rounds remain. Transient API errors and empty
    responses degrade to the next model in the chain (see ``_invoke_with_fallback``).
    """
    chain = registry.resolve_chain(state.get("selected_model"))
    tool_rounds = state.get("tool_rounds", 0)

    model_used, response, tool_calls, response_time_ms, fallback_occurred = await _invoke_with_fallback(
        chain, state["messages"], tools, tool_rounds
    )
    content = text_of(response.content)

    metadata = _extract_metadata(model_used, response, response_time_ms, tool_rounds=tool_rounds)
    metadata["fallbackOccurred"] = fallback_occurred
    # Server-log only (the metadata dict is forwarded to the browser verbatim):
    # prompt-cache hit/miss per call, to monitor the cache-adapter layer.
    usage = getattr(response, "usage_metadata", None) or {}
    input_details = usage.get("input_token_details") or {}
    logger.info(
        "Generation complete",
        extra={
            "model_used": model_used,
            "response_chars": len(content),
            "tool_calls": len(tool_calls),
            "total_tokens": metadata["totalTokens"],
            "input_tokens": usage.get("input_tokens", 0),
            "cache_read_tokens": input_details.get("cache_read", 0),
            "finish_reason": metadata["finishReason"],
            "response_time_ms": response_time_ms,
            "fallback_occurred": fallback_occurred,
        },
    )

    result: dict = {"messages": [response], "metadata": metadata}
    if not tool_calls:
        result["response"] = content
    return result
