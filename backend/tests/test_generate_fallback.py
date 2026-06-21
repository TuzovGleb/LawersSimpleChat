"""Tests for the chat generation fallback chain (nodes._invoke_with_fallback)."""
import pytest
from langchain_core.messages import AIMessage
from openai import APITimeoutError, InternalServerError

from app.pipelines.nodes import EMPTY_RESPONSE_RETRIES, _invoke_with_fallback


class FakeLLM:
    """A ChatOpenAI stand-in driving ainvoke from a list of outcomes.

    Each outcome is either an AIMessage to return or an Exception to raise.
    """

    def __init__(self, *outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    def bind_tools(self, tools):  # tools binding is transparent for these tests
        return self

    async def ainvoke(self, messages):
        outcome = self._outcomes[min(self.calls, len(self._outcomes) - 1)]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _msg(text):
    return AIMessage(content=text)


def _api_error(cls):
    # The openai error constructors need a request/response; build minimally.
    import httpx

    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    if cls is APITimeoutError:
        return cls(request=request)
    response = httpx.Response(500, request=request)
    return cls("boom", response=response, body=None)


async def test_primary_success_no_fallback():
    primary = FakeLLM(_msg("hello"))
    chain = [("openai", primary)]

    name, response, tool_calls, _ms, fallback = await _invoke_with_fallback(
        chain, ["m"], tools=None, tool_rounds=0
    )

    assert name == "openai"
    assert response.content == "hello"
    assert fallback is False
    assert primary.calls == 1


async def test_empty_retries_same_model_then_succeeds():
    # First call empty, retry on same model returns content -> no fallback.
    primary = FakeLLM(_msg("   "), _msg("recovered"))
    chain = [("openai", primary), ("gemini", FakeLLM(_msg("unused")))]

    name, response, _tc, _ms, fallback = await _invoke_with_fallback(
        chain, ["m"], tools=None, tool_rounds=0
    )

    assert name == "openai"
    assert response.content == "recovered"
    assert fallback is False
    assert primary.calls == 1 + EMPTY_RESPONSE_RETRIES


async def test_api_error_falls_back_to_next_model():
    primary = FakeLLM(_api_error(InternalServerError))
    secondary = FakeLLM(_msg("from gemini"))
    chain = [("openai", primary), ("gemini", secondary)]

    name, response, _tc, _ms, fallback = await _invoke_with_fallback(
        chain, ["m"], tools=None, tool_rounds=0
    )

    assert name == "gemini"
    assert response.content == "from gemini"
    assert fallback is True
    # Hard API error must NOT retry the same model.
    assert primary.calls == 1


async def test_persistent_empty_falls_back_after_retry():
    # Primary stays empty even after its retry; fall back to secondary.
    primary = FakeLLM(_msg(""), _msg(""))
    secondary = FakeLLM(_msg("answer"))
    chain = [("openai", primary), ("gemini", secondary)]

    name, _resp, _tc, _ms, fallback = await _invoke_with_fallback(
        chain, ["m"], tools=None, tool_rounds=0
    )

    assert name == "gemini"
    assert fallback is True
    assert primary.calls == 1 + EMPTY_RESPONSE_RETRIES


async def test_all_models_fail_raises():
    primary = FakeLLM(_api_error(APITimeoutError))
    secondary = FakeLLM(_msg(""), _msg(""))
    chain = [("openai", primary), ("gemini", secondary)]

    with pytest.raises(RuntimeError, match="All models failed"):
        await _invoke_with_fallback(chain, ["m"], tools=None, tool_rounds=0)


async def test_tool_calls_count_as_non_empty():
    response = AIMessage(content="", tool_calls=[{"name": "search", "args": {}, "id": "1"}])
    primary = FakeLLM(response)
    chain = [("openai", primary)]

    name, resp, tool_calls, _ms, fallback = await _invoke_with_fallback(
        chain, ["m"], tools=None, tool_rounds=0
    )

    assert name == "openai"
    assert tool_calls
    assert fallback is False
