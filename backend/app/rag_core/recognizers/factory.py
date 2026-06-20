"""Recognizer registry: config ``type`` -> live :class:`Recognizer`.

Mirrors ``rag_core`` builders (``get_chat``/``get_reranker``): a dict registry of
factories keyed by ``type`` and a ``@validate_call``-decorated ``get_recognizer``
that parses the raw YAML dict into the discriminated union, then constructs
recursively (``fallback`` builds its children via :func:`_construct`).
"""
from __future__ import annotations

import logging
from typing import Callable

from pydantic import validate_call

from app.rag_core.llm import build_chat_llm
from app.rag_core.recognizers.base import Recognizer
from app.rag_core.recognizers.fallback import FallbackRecognizer
from app.rag_core.recognizers.llm import LlmRecognizer
from app.rag_core.recognizers.schema import (
    FallbackParams,
    LlmRecognizerParams,
    RecognizerConfig,
    SotaOcrParams,
)
from app.rag_core.recognizers.sotaocr import SotaOcrClient, SotaOcrRecognizer

logger = logging.getLogger(__name__)


def _build_sotaocr(params: SotaOcrParams) -> Recognizer:
    return SotaOcrRecognizer(
        SotaOcrClient(
            api_key=params.api_key,
            base_url=params.base_url,
            output_format=params.output_format,
            model_profile=params.model_profile,
            poll_interval_seconds=params.poll_interval_seconds,
            job_timeout_seconds=params.job_timeout_seconds,
        )
    )


def _build_llm(params: LlmRecognizerParams) -> Recognizer:
    return LlmRecognizer(build_chat_llm(params.provider, params.model))


def _build_fallback(params: FallbackParams) -> Recognizer:
    return FallbackRecognizer([_construct(child) for child in params.chain])


# type -> builder(params). Each builder receives the already-parsed *Params model.
registry: dict[str, Callable] = {
    "sotaocr": _build_sotaocr,
    "llm": _build_llm,
    "fallback": _build_fallback,
}


def _construct(config) -> Recognizer:
    """Build from an already-validated *Config model (no re-validation)."""
    return registry[config.type](config.params)


@validate_call(config={"arbitrary_types_allowed": True})
def get_recognizer(config: RecognizerConfig) -> Recognizer:
    """Build a recognizer from raw config (validates the discriminated union)."""
    return _construct(config)


def describe(config: dict) -> str:
    """Human-readable summary of the configured recognizer chain (for startup logs)."""

    def walk(node: dict) -> str:
        node_type = node.get("type")
        if node_type == "fallback":
            chain = node.get("params", {}).get("chain", [])
            return " -> ".join(walk(c) for c in chain)
        return str(node_type)

    return walk(config)
