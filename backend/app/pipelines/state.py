"""Graph state for the chat pipeline."""
from typing import TypedDict

from langchain_core.messages import BaseMessage


class IncomingMessage(TypedDict, total=False):
    role: str
    content: str
    attachedDocumentIds: list[str]


class ChatState(TypedDict, total=False):
    # Inputs (provided per request). The frontend sends the full history every
    # turn, so context is rebuilt from the request (matches the prior Next.js
    # behaviour) rather than accumulated via a reducer.
    history: list[IncomingMessage]
    documents_by_id: dict[str, dict]
    selected_model: str

    # Built by build_context, consumed by generate. Plain (overwrite) channel;
    # the checkpointer snapshots the latest full context per thread.
    messages: list[BaseMessage]

    # Outputs.
    response: str
    metadata: dict
