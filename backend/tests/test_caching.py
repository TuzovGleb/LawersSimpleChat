"""Tests for the vendor-aware prompt-cache adapter layer (rag_core/caching.py).

The payload-level tests run messages through the REAL langchain-openai
conversion (`CachingChatOpenAI._get_request_payload`) so an upstream change
that breaks the private-API override fails loudly here.
"""
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.rag_core.caching import (
    annotate_payload_messages,
    resolve_strategy,
    session_affinity_id,
)
from app.rag_core.llm import CachingChatOpenAI, ModelConfig, ProviderConfig, build_chat_llm

CC = {"type": "ephemeral"}
CC_1H = {"type": "ephemeral", "ttl": "1h"}


def _payload_messages(*messages):
    return [dict(m) for m in messages]


# ---- resolve_strategy --------------------------------------------------------


@pytest.mark.parametrize(
    ("model_name", "caching", "expected"),
    [
        ("anthropic/claude-sonnet-4.6", "auto", "explicit"),
        ("qwen/qwen3-max", "auto", "explicit"),
        ("google/gemini-2.5-flash", "auto", "none"),  # implicit, provider-side
        ("openai/gpt-5.2", "auto", "none"),  # automatic, provider-side
        ("z-ai/glm-5", "auto", "none"),  # automatic, provider-side
        ("deepseek/deepseek-v4", "auto", "none"),
        ("unknown-vendor/some-model", "auto", "none"),
        ("anthropic/claude-sonnet-4.6", "off", "none"),
        ("google/gemini-2.5-flash", "explicit", "explicit"),
    ],
)
def test_resolve_strategy(model_name, caching, expected):
    assert resolve_strategy(model_name, caching) == expected


def test_resolve_strategy_rejects_unknown_name():
    with pytest.raises(ValueError):
        resolve_strategy("anthropic/claude-sonnet-4.6", "definitely-not-a-strategy")


# ---- explicit annotation on wire-format dicts --------------------------------


def test_explicit_marks_system_last_user_and_last_two_tools():
    messages = _payload_messages(
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "tool", "tool_call_id": "t1", "content": "res1"},
        {"role": "tool", "tool_call_id": "t2", "content": "res2"},
        {"role": "tool", "tool_call_id": "t3", "content": "res3"},
    )

    out = annotate_payload_messages("explicit", messages)

    assert out[0]["content"] == [{"type": "text", "text": "SYSTEM", "cache_control": CC_1H}]
    # Older user untouched; only the LAST user carries a breakpoint.
    assert out[1]["content"] == "u1"
    assert out[3]["content"] == [{"type": "text", "text": "u2", "cache_control": CC}]
    # Assistant untouched; oldest tool untouched; the last two tools marked —
    # this is what makes the tool loop cache incrementally.
    assert out[2]["content"] == "a1"
    assert out[4]["content"] == "res1"
    assert out[5]["content"] == [{"type": "text", "text": "res2", "cache_control": CC}]
    assert out[6]["content"] == [{"type": "text", "text": "res3", "cache_control": CC}]


def test_explicit_never_exceeds_four_breakpoints():
    messages = _payload_messages(
        {"role": "system", "content": "SYSTEM"},
        *({"role": "user", "content": f"u{i}"} for i in range(5)),
        *({"role": "tool", "tool_call_id": f"t{i}", "content": f"r{i}"} for i in range(5)),
    )

    out = annotate_payload_messages("explicit", messages)

    marked = sum(
        1
        for m in out
        if isinstance(m["content"], list)
        and any(isinstance(b, dict) and "cache_control" in b for b in m["content"])
    )
    assert marked <= 4  # Anthropic's hard limit on explicit breakpoints


def test_explicit_handles_block_list_content():
    # Multimodal user message: breakpoint goes on the LAST text block only.
    messages = _payload_messages(
        {"role": "system", "content": "SYSTEM"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "вопрос"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}},
                {"type": "text", "text": "хвост"},
            ],
        },
    )

    out = annotate_payload_messages("explicit", messages)

    blocks = out[1]["content"]
    assert "cache_control" not in blocks[0]
    assert "cache_control" not in blocks[1]
    assert blocks[2]["cache_control"] == CC


def test_explicit_skips_empty_and_textless_content():
    messages = _payload_messages(
        {"role": "system", "content": ""},
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "u"}}]},
    )

    out = annotate_payload_messages("explicit", messages)

    assert out[0]["content"] == ""
    assert out[1]["content"] == [{"type": "image_url", "image_url": {"url": "u"}}]


def test_explicit_does_not_mutate_input():
    original = {"role": "user", "content": "u1"}
    annotate_payload_messages("explicit", [original])
    assert original["content"] == "u1"


def test_none_strategy_is_identity():
    messages = _payload_messages({"role": "system", "content": "SYSTEM"})
    assert annotate_payload_messages("none", messages) is messages


# ---- through the real langchain conversion path ------------------------------


def _make_llm(**overrides) -> CachingChatOpenAI:
    params = {
        "model": "anthropic/claude-sonnet-4.6",
        "api_key": "test-key",
        "base_url": "https://openrouter.ai/api/v1",
        "cache_strategy": "explicit",
    }
    params.update(overrides)
    return CachingChatOpenAI(**params)


def _conversation():
    return [
        SystemMessage(content="SYSTEM PROMPT"),
        HumanMessage(content="вопрос 1"),
        AIMessage(content="", tool_calls=[{"id": "c1", "name": "t", "args": {}}]),
        ToolMessage(content="результат", tool_call_id="c1", name="t"),
        AIMessage(content="ответ 1"),
        HumanMessage(content="вопрос 2"),
    ]


def test_payload_annotated_for_explicit_strategy():
    payload = _make_llm()._get_request_payload(_conversation())

    system = payload["messages"][0]
    assert system["role"] in ("system", "developer")
    assert system["content"][0]["cache_control"] == CC_1H

    users = [m for m in payload["messages"] if m["role"] == "user"]
    assert users[-1]["content"][-1]["cache_control"] == CC
    assert "cache_control" not in str(users[0]["content"]) or len(users) == 1

    # Tool results ARE marked (post-conversion, past langchain's sanitizer) —
    # the incremental intra-turn cache rides on these.
    tool = next(m for m in payload["messages"] if m["role"] == "tool")
    assert tool["content"][-1]["cache_control"] == CC


def test_payload_untouched_for_none_strategy():
    payload = _make_llm(cache_strategy="none")._get_request_payload(_conversation())
    assert "cache_control" not in str(payload)


def test_payload_annotation_survives_bind_tools():
    from langchain_core.tools import tool

    @tool
    def dummy(query: str) -> str:
        """A dummy tool."""
        return query

    bound = _make_llm().bind_tools([dummy])
    # RunnableBinding delegates payload building to the underlying model.
    payload = bound.bound._get_request_payload(_conversation(), **bound.kwargs)
    assert payload["messages"][0]["content"][0]["cache_control"] == CC_1H
    assert payload["tools"], "tools must still be present in the payload"


# ---- provider affinity (OpenRouter session_id) --------------------------------


def test_payload_carries_session_id_from_context():
    token = session_affinity_id.set("chat-123")
    try:
        payload = _make_llm()._get_request_payload(_conversation())
        assert payload["extra_body"]["session_id"] == "chat-123"
        # Affinity helps automatic-caching vendors too — injected for "none".
        payload = _make_llm(cache_strategy="none")._get_request_payload(_conversation())
        assert payload["extra_body"]["session_id"] == "chat-123"
    finally:
        session_affinity_id.reset(token)


def test_session_id_merges_with_existing_extra_body():
    # The web-search plugin already rides in extra_body — it must survive.
    token = session_affinity_id.set("chat-123")
    try:
        llm = _make_llm(extra_body={"plugins": [{"id": "web", "max_results": 5}]})
        payload = llm._get_request_payload(_conversation())
        assert payload["extra_body"]["plugins"] == [{"id": "web", "max_results": 5}]
        assert payload["extra_body"]["session_id"] == "chat-123"
    finally:
        session_affinity_id.reset(token)


def test_payload_has_no_session_id_outside_chat_context():
    payload = _make_llm()._get_request_payload(_conversation())
    assert "session_id" not in str(payload.get("extra_body", ""))


# ---- build_chat_llm wiring ----------------------------------------------------


def _provider() -> ProviderConfig:
    return ProviderConfig(api_key="test-key")


def test_build_chat_llm_resolves_vendor_strategy():
    llm = build_chat_llm(_provider(), ModelConfig(name="anthropic/claude-sonnet-4.6"))
    assert isinstance(llm, CachingChatOpenAI)
    assert llm.cache_strategy == "explicit"

    llm = build_chat_llm(_provider(), ModelConfig(name="google/gemini-2.5-flash"))
    assert llm.cache_strategy == "none"


def test_build_chat_llm_honours_off():
    llm = build_chat_llm(
        _provider(), ModelConfig(name="anthropic/claude-sonnet-4.6", caching="off")
    )
    assert llm.cache_strategy == "none"


def test_drafting_llm_caching_disabled():
    from app.rag_core.llm import ChatProviderParams
    from app.services.docx_drafting import build_drafting_llm

    params = ChatProviderParams(
        provider=_provider(),
        default_model="fast",
        models={"fast": ModelConfig(name="anthropic/claude-sonnet-4.6")},
    )
    llm = build_drafting_llm(params)
    assert llm.cache_strategy == "none"


def test_build_chat_llm_provider_pin_merges_with_plugins():
    from app.rag_core.llm import WebSearchConfig

    llm = build_chat_llm(
        _provider(),
        ModelConfig(
            name="anthropic/claude-sonnet-4.6",
            provider_order=["anthropic"],
            web_search=WebSearchConfig(enabled=True, max_results=5),
        ),
    )
    assert llm.extra_body["provider"] == {"order": ["anthropic"], "allow_fallbacks": False}
    assert llm.extra_body["plugins"] == [{"id": "web", "max_results": 5}]

    # And session affinity must not clobber the pin at request-build time.
    token = session_affinity_id.set("chat-9")
    try:
        payload = llm._get_request_payload(_conversation())
        assert payload["extra_body"]["provider"]["order"] == ["anthropic"]
        assert payload["extra_body"]["session_id"] == "chat-9"
        assert payload["extra_body"]["plugins"]
    finally:
        session_affinity_id.reset(token)
