"""Chat pipeline assembly (LangGraph).

History is owned by the application DB and rebuilt fresh every turn in
``build_context``; there is no checkpointer. The ``add_messages`` reducer is
still used, but only to accumulate the tool loop *within* a single graph run.
"""
from functools import partial

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from app.pipelines.nodes import (
    build_context,
    generate,
    increment_tool_rounds,
    route_after_generate,
    route_after_tools,
)
from app.pipelines.state import ChatState
from app.pipelines.tools.base import ToolSpec, handlers_of, terminal_tool_names, tools_of
from app.rag_core.llm import ChatModelRegistry


def build_chat_graph(registry: ChatModelRegistry, specs: list[ToolSpec] | None = None):
    """START -> build_context -> generate <-> tools -> END."""
    specs = specs or []
    tools = tools_of(specs)
    handlers = handlers_of(specs)

    workflow = StateGraph(ChatState)
    workflow.add_node("build_context", partial(build_context, handlers=handlers))
    workflow.add_node("generate", partial(generate, registry=registry, tools=tools))

    workflow.add_edge(START, "build_context")
    workflow.add_edge("build_context", "generate")

    if tools:
        terminal_names = terminal_tool_names(specs)
        workflow.add_node("increment_tool_rounds", increment_tool_rounds)
        workflow.add_node("tools", ToolNode(tools))
        workflow.add_conditional_edges(
            "generate",
            route_after_generate,
            {"tools": "increment_tool_rounds", "end": END},
        )
        workflow.add_edge("increment_tool_rounds", "tools")
        # A terminal tool ends the turn; everything else loops back to generate.
        workflow.add_conditional_edges(
            "tools",
            partial(route_after_tools, terminal_names=terminal_names),
            {"generate": "generate", "end": END},
        )
    else:
        workflow.add_edge("generate", END)

    return workflow.compile()
