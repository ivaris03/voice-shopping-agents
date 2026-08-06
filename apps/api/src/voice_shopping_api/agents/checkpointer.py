import asyncio
import logging
import sys
from contextlib import AsyncExitStack, suppress

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from voice_shopping_api.core.config import get_settings

logger = logging.getLogger(__name__)


def _uses_windows_proactor_loop() -> bool:
    if sys.platform != "win32":
        return False
    proactor_loop = getattr(asyncio, "ProactorEventLoop", None)
    return proactor_loop is not None and isinstance(asyncio.get_running_loop(), proactor_loop)


class LazyPostgresCheckpointer:
    """Open one Postgres checkpointer on the first workflow invocation."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._resources = AsyncExitStack()
        self._checkpointer: AsyncPostgresSaver | None = None
        self._disabled_for_event_loop = False

    async def get(self) -> BaseCheckpointSaver | None:
        settings = get_settings()
        if not settings.langgraph_checkpoint_enabled:
            return None
        if self._disabled_for_event_loop:
            return None
        if self._checkpointer is not None:
            return self._checkpointer
        async with self._lock:
            if self._disabled_for_event_loop:
                return None
            if self._checkpointer is not None:
                return self._checkpointer
            if _uses_windows_proactor_loop():
                self._disabled_for_event_loop = True
                logger.warning(
                    "Disabling the LangGraph Postgres checkpointer because the current "
                    "Windows Proactor event loop is incompatible. Use the supported "
                    "voice_shopping_api.server launcher to enable it."
                )
                return None
            try:
                async with asyncio.timeout(
                    settings.langgraph_checkpoint_init_timeout_seconds
                ):
                    checkpointer = await self._resources.enter_async_context(
                        AsyncPostgresSaver.from_conn_string(settings.langgraph_checkpoint_url)
                    )
                    await checkpointer.setup()
            except TimeoutError:
                logger.warning(
                    "LangGraph Postgres checkpointer initialization timed out after %.1fs; "
                    "falling back to session state persistence",
                    settings.langgraph_checkpoint_init_timeout_seconds,
                )
                await self._disable_after_failure()
                return None
            except Exception:
                logger.exception(
                    "LangGraph Postgres checkpointer initialization failed; "
                    "falling back to session state persistence"
                )
                await self._disable_after_failure()
                return None
            self._checkpointer = checkpointer
            return checkpointer

    async def _disable_after_failure(self) -> None:
        self._disabled_for_event_loop = True
        with suppress(Exception):
            await self._resources.aclose()
        self._resources = AsyncExitStack()

    async def close(self) -> None:
        async with self._lock:
            await self._resources.aclose()
            self._resources = AsyncExitStack()
            self._checkpointer = None
            self._disabled_for_event_loop = False


_checkpointer = LazyPostgresCheckpointer()


async def get_checkpointer() -> BaseCheckpointSaver | None:
    return await _checkpointer.get()


async def close_checkpointer() -> None:
    await _checkpointer.close()
