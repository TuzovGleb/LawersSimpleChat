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

from typing import Callable

# A strategy takes the OpenAI-format message dicts and returns the (possibly
# rewritten) list. It must not mutate the input blocks in place: bind_tools
# and retries may rebuild payloads from shared structures.
CacheStrategy = Callable[[list[dict]], list[dict]]

# Anthropic allows at most 4 explicit breakpoints per request. We spend 3:
# one on the system prompt (caches tools+system — tools render before system
# in Anthropic's prefix) and one on each of the last two user messages. The
# previous-turn breakpoint guarantees a cache hit across turns even when the
# current turn appended more blocks than the provider's lookback window.
_TRAILING_USER_BREAKPOINTS = 2

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
    """Anthropic-style explicit breakpoints: system + the last two user turns."""
    annotated = [dict(message) for message in messages]

    user_indexes = [i for i, m in enumerate(annotated) if m.get("role") == "user"]
    targets = set(user_indexes[-_TRAILING_USER_BREAKPOINTS:])
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
