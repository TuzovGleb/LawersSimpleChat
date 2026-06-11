"""Chat pipeline assembly (LangGraph)."""
from functools import partial
from typing import Sequence

from langchain_core.tools import BaseTool
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
from app.rag_core.llm import ChatModelRegistry


def build_chat_graph(
    registry: ChatModelRegistry,
    checkpointer: BaseCheckpointSaver,
    tools: Sequence[BaseTool] = (),
):
    """START -> build_context -> generate <-> tools -> END."""
    workflow = StateGraph(ChatState)

    workflow.add_node("build_context", build_context)
    workflow.add_node("generate", partial(generate, registry=registry, tools=list(tools)))

    workflow.add_edge(START, "build_context")
    workflow.add_edge("build_context", "generate")

    if tools:
        workflow.add_node("increment_tool_rounds", increment_tool_rounds)
        workflow.add_node("tools", ToolNode(tools))
        workflow.add_conditional_edges(
            "generate",
            route_after_generate,
            {"tools": "increment_tool_rounds", "end": END},
        )
        workflow.add_edge("increment_tool_rounds", "tools")
        workflow.add_edge("tools", "generate")
    else:
        workflow.add_edge("generate", END)

    return workflow.compile(checkpointer=checkpointer)
