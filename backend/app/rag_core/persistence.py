"""LangGraph checkpointer factory.

The checkpointer stores conversation state per ``thread_id`` (== chat session
id). ``memory`` is process-local; ``postgres`` persists across restarts and
multiple instances. Postgres setup must run inside an async context, so it is
created from the FastAPI lifespan via an AsyncExitStack.
"""
from contextlib import AsyncExitStack
import logging
from typing import Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class CheckpointerConfig(BaseModel):
    type: Literal["memory", "postgres"] = "memory"
    conn_string: str | None = None


async def build_checkpointer(
    config: CheckpointerConfig, stack: AsyncExitStack
) -> BaseCheckpointSaver:
    if config.type == "postgres":
        if not config.conn_string:
            raise ValueError("CHECKPOINTER_DB_URL is required when CHECKPOINTER_TYPE=postgres")
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        checkpointer = await stack.enter_async_context(
            AsyncPostgresSaver.from_conn_string(config.conn_string)
        )
        await checkpointer.setup()
        logger.info("Using Postgres checkpointer for conversation state")
        return checkpointer

    logger.info("Using in-memory checkpointer for conversation state")
    return MemorySaver()
