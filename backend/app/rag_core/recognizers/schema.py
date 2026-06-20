"""Pydantic configs for the recognizer registry (discriminated union by ``type``).

One ``*Config`` per recognizer variant, combined into :data:`RecognizerConfig`.
``fallback`` is recursive — its ``chain`` is a list of nested ``RecognizerConfig``
— so a config can compose arbitrary recognizer pipelines from YAML alone.
"""
from __future__ import annotations

from typing import Annotated, List, Literal, Union

from pydantic import BaseModel, Field

from app.rag_core.llm import ModelConfig, ProviderConfig


class SotaOcrParams(BaseModel):
    api_key: str = ""
    base_url: str = "https://sotaocr.com"
    # Result projection requested from SotaOCR: markdown preserves tables/layout
    # (best for legal docs); text is plain.
    output_format: Literal["markdown", "text"] = "markdown"
    # SotaOCR scan profile. "fast" = 1 scan page/page, "pro" = 2 (higher quality).
    # Empty string => omit the field and let the server pick its default.
    model_profile: str = "fast"
    # Poll the job no more than once per second (SotaOCR docs).
    poll_interval_seconds: float = 1.0
    # Hard ceiling on a single document's submit→poll→fetch cycle.
    job_timeout_seconds: float = 1800.0


class SotaOcrConfig(BaseModel):
    type: Literal["sotaocr"]
    params: SotaOcrParams


class LlmRecognizerParams(BaseModel):
    # Reuses the chat provider/model schema so the OpenRouter OCR model is
    # configured exactly like the chat models (api_key/base_url/headers/web_search).
    provider: ProviderConfig
    model: ModelConfig


class LlmRecognizerConfig(BaseModel):
    type: Literal["llm"]
    params: LlmRecognizerParams


class FallbackParams(BaseModel):
    # Tried in order; first non-empty result wins.
    chain: List["RecognizerConfig"]


class FallbackConfig(BaseModel):
    type: Literal["fallback"]
    params: FallbackParams


RecognizerConfig = Annotated[
    Union[SotaOcrConfig, LlmRecognizerConfig, FallbackConfig],
    Field(discriminator="type"),
]

# Resolve the forward reference in FallbackParams.chain now that RecognizerConfig
# exists (recursive discriminated union).
FallbackParams.model_rebuild()
