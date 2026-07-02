"""LLM factory for OpenRouter-backed chat models.

Mirrors lib/model-config.ts: each selectable model maps to an OpenRouter model
name, optional reasoning effort, and an optional web-search plugin. Web search
is passed through OpenRouter's ``plugins`` request field, exactly like the
Next.js implementation (lib/response-chunker.ts).
"""
from functools import lru_cache
from typing import Literal

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field


class WebSearchConfig(BaseModel):
    enabled: bool = False
    max_results: int = 5


class ModelConfig(BaseModel):
    name: str
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    web_search: WebSearchConfig = Field(default_factory=WebSearchConfig)
    # Ordered list of other model keys to try if this one errors or returns an
    # empty response. Unknown keys are ignored at resolve time.
    fallback: list[str] = Field(default_factory=list)


class ProviderConfig(BaseModel):
    api_key: str
    base_url: str = "https://openrouter.ai/api/v1"
    site_url: str | None = None
    app_title: str | None = None


class ChatProviderParams(BaseModel):
    provider: ProviderConfig
    default_model: str = "fast"
    models: dict[str, ModelConfig]


def build_chat_llm(provider: ProviderConfig, model: ModelConfig) -> ChatOpenAI:
    default_headers: dict[str, str] = {}
    if provider.site_url:
        default_headers["HTTP-Referer"] = provider.site_url
    if provider.app_title:
        default_headers["X-Title"] = provider.app_title

    kwargs: dict = {
        "model": model.name,
        "api_key": provider.api_key,
        "base_url": provider.base_url,
        "max_retries": 2,
        # Per-call ceiling (heavy legal queries can run long), but well under the
        # Yandex Serverless Container --execution-timeout of 1800s so a stalled
        # upstream errors out and closes its LangSmith run instead of hanging.
        # SERVERLESS NOTE: this "stay under the platform timeout" tuning only
        # exists because of the serverless --execution-timeout; on a normal server
        # we'd just pick a sane upstream timeout and be done.
        "timeout": 300,
        # Stream tokens so the chat endpoint can forward them live over SSE.
        # ainvoke still returns the aggregated AIMessage, so tool-call
        # aggregation, the fallback chain and metadata in nodes.generate are all
        # unchanged; the deltas surface via LangGraph stream_mode="messages".
        # SERVERLESS NOTE: streaming is fully wired here, but Yandex Serverless
        # buffers the HTTP response, so the deltas don't reach the browser live.
        # On a regular VM this gives a true token-by-token stream, no code change.
        "streaming": True,
        "stream_usage": True,
    }
    if model.temperature is not None:
        kwargs["temperature"] = model.temperature
    if model.max_tokens is not None:
        kwargs["max_tokens"] = model.max_tokens
    if model.reasoning_effort:
        kwargs["reasoning_effort"] = model.reasoning_effort
    if default_headers:
        kwargs["default_headers"] = default_headers
    if model.web_search.enabled:
        # OpenRouter-specific params are not part of the OpenAI API surface, so
        # they must go through extra_body (model_kwargs would be passed as
        # top-level kwargs to the SDK and rejected).
        kwargs["extra_body"] = {
            "plugins": [{"id": "web", "max_results": model.web_search.max_results}]
        }

    return ChatOpenAI(**kwargs)


class ChatModelRegistry:
    """Pre-builds one ChatOpenAI per selectable model and resolves by name."""

    def __init__(self, params: ChatProviderParams):
        self._params = params
        self._llms: dict[str, ChatOpenAI] = {
            name: build_chat_llm(params.provider, cfg)
            for name, cfg in params.models.items()
        }

    @property
    def default_model(self) -> str:
        return self._params.default_model

    @property
    def params(self) -> ChatProviderParams:
        return self._params

    def resolve(self, selected_model: str | None) -> tuple[str, ChatOpenAI]:
        name = selected_model if selected_model in self._llms else self._params.default_model
        return name, self._llms[name]

    def resolve_chain(self, selected_model: str | None) -> list[tuple[str, ChatOpenAI]]:
        """Resolve the primary model plus its configured fallbacks, in order.

        The first entry is the selected model (or the default if unknown); the
        rest are its ``fallback`` keys, de-duplicated and filtered to ones that
        actually exist in the registry.
        """
        primary = selected_model if selected_model in self._llms else self._params.default_model
        names = [primary]
        for fb in self._params.models[primary].fallback:
            if fb in self._llms and fb not in names:
                names.append(fb)
        return [(name, self._llms[name]) for name in names]


@lru_cache(maxsize=1)
def _cached_registry(serialized: str) -> ChatModelRegistry:  # pragma: no cover
    return ChatModelRegistry(ChatProviderParams.model_validate_json(serialized))


def get_chat_registry(params: dict) -> ChatModelRegistry:
    """Build (and cache) the model registry from raw config params."""
    validated = ChatProviderParams.model_validate(params)
    return _cached_registry(validated.model_dump_json())
