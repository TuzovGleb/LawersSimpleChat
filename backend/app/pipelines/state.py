"""Graph state for the chat pipeline."""
from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class IncomingMessage(TypedDict, total=False):
    role: str
    content: str
    attachedDocumentIds: list[str]


class ChatState(TypedDict, total=False):
    # Inputs (provided per request).
    history: list[IncomingMessage]
    documents_by_id: dict[str, dict]
    selected_model: str

    # Built by build_context, extended by generate/tools via add_messages reducer.
    messages: Annotated[list[BaseMessage], add_messages]
    tool_rounds: int

    # Outputs.
    response: str
    metadata: dict
