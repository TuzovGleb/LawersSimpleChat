"""Request/response models for REST chat endpoints.

POST /chats/{chat_id}/messages — chat_id comes from the URL path, not the body.
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
    userId: str | None = None
    projectId: str | None = None
    selectedModel: str | None = None
    utm: dict | None = None

    model_config = {"extra": "ignore"}
