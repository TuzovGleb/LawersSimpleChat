"""Request/response models for the chat endpoint.

The request shape matches the body the Next.js frontend already sends to
/api/chat, so the proxy can forward it unchanged.
"""
from typing import Literal

from pydantic import BaseModel, Field


class ChatMessageIn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = ""
    attachedDocumentIds: list[str] = Field(default_factory=list)

    model_config = {"extra": "ignore"}


class ChatRequest(BaseModel):
    messages: list[ChatMessageIn] = Field(min_length=1)
    sessionId: str | None = None
    userId: str | None = None
    projectId: str | None = None
    selectedModel: str | None = None
    utm: dict | None = None

    model_config = {"extra": "ignore"}
