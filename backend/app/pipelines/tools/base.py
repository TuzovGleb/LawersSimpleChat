"""Domain-agnostic tool framework.

A tool contributes two things to the chat pipeline:

* a LangChain ``BaseTool`` — executed live by the graph and bound to the LLM;
* a :class:`ToolResultHandler` — owns how that tool's result is stored at the
  end of a turn (:meth:`~ToolResultHandler.capture`) and how it is reproduced
  for the model's context on later turns (:meth:`~ToolResultHandler.run`).

Nothing here knows about any specific domain (court practice, OpenSearch, …).
Domains plug in by returning :class:`ToolSpec` objects from their own module.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from langchain_core.tools import BaseTool


class ToolResultHandler(ABC):
    """Bridge between a live tool result and its persisted form."""

    @abstractmethod
    async def run(self, *, args: dict, state: dict) -> str:
        """Produce the tool-message content injected into the model's context.

        ``args`` are the original call arguments; ``state`` is whatever
        :meth:`capture` stashed. Free to do any async work — fetch from a store,
        call an API, recompute, or just echo cached text.
        """

    async def capture(self, *, args: dict, content: str) -> dict:
        """Return the state to persist alongside the call.

        Default keeps the live result verbatim, so a tool needs a custom
        handler only when it wants something cheaper than the full payload.
        """
        return {"content": content}


class InlineResultHandler(ToolResultHandler):
    """Store the live result and replay it as-is. The default for any tool."""

    async def run(self, *, args: dict, state: dict) -> str:
        return state.get("content", "")


@dataclass(frozen=True)
class ToolSpec:
    """A tool plus the handler that governs its persistence/replay.

    ``terminal`` makes the agent loop END after this tool runs instead of
    looping back to ``generate``. Terminal-ness is a hardcoded property of the
    tool (not a per-call model decision): a terminal tool *is* the turn's answer
    (e.g. it produces a downloadable document), so there is nothing for the model
    to summarize afterwards.
    """

    tool: BaseTool
    handler: ToolResultHandler = field(default_factory=InlineResultHandler)
    terminal: bool = False


def tools_of(specs: list[ToolSpec]) -> list[BaseTool]:
    return [spec.tool for spec in specs]


def handlers_of(specs: list[ToolSpec]) -> dict[str, ToolResultHandler]:
    return {spec.tool.name: spec.handler for spec in specs}


def terminal_tool_names(specs: list[ToolSpec]) -> frozenset[str]:
    """Names of tools that end the turn instead of looping back to generate."""
    return frozenset(spec.tool.name for spec in specs if spec.terminal)
