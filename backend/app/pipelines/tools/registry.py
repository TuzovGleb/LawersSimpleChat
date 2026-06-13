"""Tool registry: assemble every enabled tool suite from config.

The app's composition root calls :func:`load_tool_specs` and stays blind to how
any tool is wired. Each builder reads its own slice of config, constructs the
clients it needs, and returns ``[]`` when it is not configured. To add a tool
suite, append its builder here — nothing else changes.
"""
from typing import Callable

from app.pipelines.tools.base import ToolSpec
from app.pipelines.tools.court_practice import try_build_tool_specs as court_practice_builder

# Each builder: (app_config) -> list[ToolSpec], returning [] when not configured.
TOOL_BUILDERS: list[Callable[[dict], list[ToolSpec]]] = [
    court_practice_builder,
]


def load_tool_specs(app_config: dict) -> list[ToolSpec]:
    specs: list[ToolSpec] = []
    for builder in TOOL_BUILDERS:
        specs.extend(builder(app_config))
    return specs
