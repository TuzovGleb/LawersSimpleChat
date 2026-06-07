"""Chat graph nodes: context assembly and generation."""
import logging
import time

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.rag_core.llm import ChatModelRegistry
from app.rag_core.prompt import get_system_prompt

logger = logging.getLogger(__name__)

MAX_CONTEXT_DOCUMENTS = 20


def _format_with_attachments(message: dict, documents_by_id: dict[str, dict]) -> str:
    content = message.get("content") or ""
    if message.get("role") != "user":
        return content

    attachment_ids = [
        doc_id
        for doc_id in (message.get("attachedDocumentIds") or [])
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

    attachments_block = "[Прикрепленные документы к этому сообщению]\n\n" + "\n\n---\n\n".join(prepared)
    if not content.strip():
        return attachments_block
    return f"{content}\n\n{attachments_block}"


def build_context(state: dict) -> dict:
    """Assemble [system, ...history] with per-message document injection."""
    history = state.get("history") or []
    documents_by_id = state.get("documents_by_id") or {}

    messages: list = [SystemMessage(content=get_system_prompt())]
    for message in history:
        content = _format_with_attachments(message, documents_by_id)
        if message.get("role") == "assistant":
            messages.append(AIMessage(content=content))
        else:
            messages.append(HumanMessage(content=content))

    logger.info(
        "Context assembled",
        extra={
            "history_len": len(history),
            "attached_documents": len(documents_by_id),
            "selected_model": state.get("selected_model"),
        },
    )
    return {"messages": messages}


def _extract_metadata(model_used: str, response: AIMessage, response_time_ms: int) -> dict:
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
    }


async def generate(state: dict, *, registry: ChatModelRegistry) -> dict:
    """Call the selected OpenRouter model and capture response metadata."""
    model_used, llm = registry.resolve(state.get("selected_model"))
    started = time.time()

    response = await llm.ainvoke(state["messages"])

    response_time_ms = int((time.time() - started) * 1000)
    content = response.content if isinstance(response.content, str) else str(response.content)
    metadata = _extract_metadata(model_used, response, response_time_ms)

    logger.info(
        "Generation complete",
        extra={
            "model_used": model_used,
            "response_chars": len(content),
            "total_tokens": metadata["totalTokens"],
            "finish_reason": metadata["finishReason"],
            "response_time_ms": response_time_ms,
        },
    )

    if not content or not content.strip():
        raise RuntimeError("Empty response from model")

    full_thread = list(state["messages"]) + [response]
    return {"response": content, "metadata": metadata, "messages": full_thread}
