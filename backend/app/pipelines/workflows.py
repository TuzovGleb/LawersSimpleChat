"""Chat pipeline assembly (LangGraph)."""
from functools import partial

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from app.pipelines.nodes import (
    build_context,
    generate,
    increment_tool_rounds,
    route_after_generate,
)
from app.pipelines.state import ChatState
from app.pipelines.tools import COURT_PRACTICE_TOOLS
from app.rag_core.llm import ChatModelRegistry


def build_chat_graph(registry: ChatModelRegistry, checkpointer: BaseCheckpointSaver):
    """START -> build_context -> generate <-> tools -> END."""
    workflow = StateGraph(ChatState)

    tool_node = ToolNode(COURT_PRACTICE_TOOLS)

    workflow.add_node("build_context", build_context)
    workflow.add_node("generate", partial(generate, registry=registry))
    workflow.add_node("increment_tool_rounds", increment_tool_rounds)
    workflow.add_node("tools", tool_node)

    workflow.add_edge(START, "build_context")
    workflow.add_edge("build_context", "generate")
    workflow.add_conditional_edges(
        "generate",
        route_after_generate,
        {"tools": "increment_tool_rounds", "end": END},
    )
    workflow.add_edge("increment_tool_rounds", "tools")
    workflow.add_edge("tools", "generate")

    return workflow.compile(checkpointer=checkpointer)
