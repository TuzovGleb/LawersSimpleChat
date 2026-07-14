"""Vendor-aware prompt-cache annotation for OpenRouter chat models.

Mirrors the recognizer registry (``recognizers/factory.py``): a dict registry
of strategies keyed by name, plus a resolver that derives the default strategy
from the OpenRouter model id's vendor prefix (``anthropic/...`` -> explicit
breakpoints, ``google/...`` -> provider-side implicit caching, etc.).

Strategies operate on the OpenAI wire-format ``payload["messages"]`` dicts —
AFTER langchain's message conversion — for two reasons:

* langchain-openai strips unknown keys from AI/Tool message content blocks
  during conversion, so annotating ``BaseMessage`` objects is fragile;
* the chat fallback chain crosses vendors (anthropic -> google), and the graph
  passes ONE shared message list to every candidate. Annotating inside each
  model's own payload build keeps vendor-specific markup out of shared state.

Economics (verified live against OpenRouter, 2026-07-14): Anthropic cache
writes bill at 1.25x input price, reads at 0.1x; a prompt below the model's
minimum cacheable size is silently not cached (and not surcharged), so
annotating small prompts is harmless.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Callable

# OpenRouter routes each request to one of several providers serving the same
# model, and every provider keeps its OWN prompt cache. Without affinity the
# first requests of a conversation play provider roulette: a prefix written on
# provider A misses when the next call lands on provider B (observed live:
# intra-turn cache_read=0 on calls whose prefix was written seconds earlier).
# OpenRouter's ``session_id`` pins the provider from the FIRST successful
# request (without it, sticky routing engages only after a lucky cache hit).
#
# The chat handler sets this to the conversation id before running the graph;
# asyncio tasks inherit the context, so every LLM call of the turn — including
# the drafting tool's — carries the same affinity key.
session_affinity_id: ContextVar[str | None] = ContextVar("session_affinity_id", default=None)

# A strategy takes the OpenAI-format message dicts and returns the (possibly
# rewritten) list. It must not mutate the input blocks in place: bind_tools
# and retries may rebuild payloads from shared structures.
CacheStrategy = Callable[[list[dict]], list[dict]]

# Anthropic allows at most 4 explicit breakpoints per request. Allocation:
# 1 on the system prompt (caches tools+system — tools render before system in
# Anthropic's prefix), 1 on the last user message, and 1 on each of the last
# two TOOL results. Tool-result breakpoints are what make the tool loop
# incremental — iteration N+1 reads everything through iteration N's newest
# tool result instead of re-paying the whole history — and they are the part
# that stays robust when the OpenRouter web plugin (engine=exa) injects
# volatile search results into the latest user message: the polluted segment
# then costs at most one re-write, while the chain of tool entries keeps
# caching everything downstream of it (verified live 2026-07-14; the plugin
# rewrote ~4.5k tokens per call with different bytes each time). At a turn
# boundary the previous turn's tool breakpoints double as deep read points
# for the new turn's first call.
_TRAILING_USER_BREAKPOINTS = 1
_TRAILING_TOOL_BREAKPOINTS = 2

_CACHE_CONTROL = {"type": "ephemeral"}


def _cached_content(content) -> list[dict] | None:
    """Content -> block list with ``cache_control`` on the last text block.

    Returns ``None`` when there is nothing to annotate (empty content or a
    block list without a single text block).
    """
    if isinstance(content, str):
        if not content:
            return None
        return [{"type": "text", "text": content, "cache_control": dict(_CACHE_CONTROL)}]
    if isinstance(content, list):
        blocks = [dict(block) if isinstance(block, dict) else block for block in content]
        for block in reversed(blocks):
            if isinstance(block, dict) and block.get("type") == "text":
                block["cache_control"] = dict(_CACHE_CONTROL)
                return blocks
    return None


def _annotate_explicit(messages: list[dict]) -> list[dict]:
    """Anthropic-style breakpoints: system + last user + last two tool results."""
    annotated = [dict(message) for message in messages]

    user_indexes = [i for i, m in enumerate(annotated) if m.get("role") == "user"]
    tool_indexes = [i for i, m in enumerate(annotated) if m.get("role") == "tool"]
    targets = set(user_indexes[-_TRAILING_USER_BREAKPOINTS:])
    targets.update(tool_indexes[-_TRAILING_TOOL_BREAKPOINTS:])
    targets.update(i for i, m in enumerate(annotated) if m.get("role") in ("system", "developer"))

    for index in targets:
        content = _cached_content(annotated[index].get("content"))
        if content is not None:
            annotated[index]["content"] = content
    return annotated


def _annotate_none(messages: list[dict]) -> list[dict]:
    return messages


# strategy name -> annotator. "none" also covers vendors whose caching is
# provider-side automatic/implicit and needs no request markup.
registry: dict[str, CacheStrategy] = {
    "explicit": _annotate_explicit,
    "none": _annotate_none,
}

# Vendor prefix of the OpenRouter model id -> default strategy under
# ``caching: auto``. Vendors absent here cache automatically on the provider
# side (openai, google, deepseek, x-ai, moonshotai, z-ai) or not at all —
# either way no request markup is needed, so they resolve to "none".
_VENDOR_DEFAULTS: dict[str, str] = {
    "anthropic": "explicit",
    "qwen": "explicit",
    "alibaba": "explicit",
}


def resolve_strategy(model_name: str, caching: str = "auto") -> str:
    """Resolve a ``ModelConfig.caching`` value to a registry strategy name.

    ``auto`` derives from the model id's vendor prefix, ``off`` disables
    markup, and any explicit registry key is passed through.
    """
    if caching == "off":
        return "none"
    if caching == "auto":
        vendor = model_name.split("/", 1)[0].lower()
        return _VENDOR_DEFAULTS.get(vendor, "none")
    if caching in registry:
        return caching
    raise ValueError(f"Unknown caching strategy: {caching!r}")


def annotate_payload_messages(strategy: str, messages: list[dict]) -> list[dict]:
    return registry[strategy](messages)
