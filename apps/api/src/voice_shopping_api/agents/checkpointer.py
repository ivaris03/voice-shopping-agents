import asyncio
import sys
from contextlib import AsyncExitStack

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from voice_shopping_api.core.config import get_settings


class LazyPostgresCheckpointer:
    """Open one Postgres checkpointer on the first workflow invocation."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._resources = AsyncExitStack()
        self._checkpointer: AsyncPostgresSaver | None = None

    async def get(self) -> BaseCheckpointSaver | None:
        settings = get_settings()
        if not settings.langgraph_checkpoint_enabled:
            return None
        if self._checkpointer is not None:
            return self._checkpointer
        async with self._lock:
            if self._checkpointer is not None:
                return self._checkpointer
            if sys.platform == "win32" and isinstance(
                asyncio.get_running_loop(), asyncio.ProactorEventLoop
            ):
                raise RuntimeError(
                    "LangGraph Postgres checkpointer requires a Selector event loop on Windows. "
                    "Run Uvicorn with --reload or configure a selector loop."
                )
            try:
                checkpointer = await self._resources.enter_async_context(
                    AsyncPostgresSaver.from_conn_string(settings.langgraph_checkpoint_url)
                )
                await checkpointer.setup()
            except Exception:
                await self._resources.aclose()
                self._resources = AsyncExitStack()
                raise
            self._checkpointer = checkpointer
            return checkpointer

    async def close(self) -> None:
        async with self._lock:
            await self._resources.aclose()
            self._resources = AsyncExitStack()
            self._checkpointer = None


_checkpointer = LazyPostgresCheckpointer()


async def get_checkpointer() -> BaseCheckpointSaver | None:
    return await _checkpointer.get()


async def close_checkpointer() -> None:
    await _checkpointer.close()
