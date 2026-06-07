"""Chat pipeline assembly (LangGraph)."""
from functools import partial

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.pipelines.nodes import build_context, generate
from app.pipelines.state import ChatState
from app.rag_core.llm import ChatModelRegistry


def build_chat_graph(registry: ChatModelRegistry, checkpointer: BaseCheckpointSaver):
    """START -> build_context -> generate -> END.

    Kept deliberately small; this is the seam where a future ``retrieve`` (RAG)
    node will be inserted before ``generate``.
    """
    workflow = StateGraph(ChatState)

    workflow.add_node("build_context", build_context)
    workflow.add_node("generate", partial(generate, registry=registry))

    workflow.add_edge(START, "build_context")
    workflow.add_edge("build_context", "generate")
    workflow.add_edge("generate", END)

    return workflow.compile(checkpointer=checkpointer)
