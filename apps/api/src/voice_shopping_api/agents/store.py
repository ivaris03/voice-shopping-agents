import asyncio
import logging
from contextlib import AsyncExitStack, suppress

from langgraph.store.base import BaseStore
from langgraph.store.postgres.aio import AsyncPostgresStore

from voice_shopping_api.agents.checkpointer import _uses_windows_proactor_loop
from voice_shopping_api.agents.model import embed_query
from voice_shopping_api.core.config import get_settings

logger = logging.getLogger(__name__)


async def _embed_texts(texts: list[str]) -> list[list[float]]:
    """Adapt the application's async embedding gateway to LangGraph Store."""
    return await asyncio.gather(*(embed_query(value) for value in texts))


class LazyPostgresStore:
    """Open one long-term memory store on the first workflow invocation."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._resources = AsyncExitStack()
        self._store: AsyncPostgresStore | None = None
        self._disabled_for_event_loop = False

    async def get(self) -> BaseStore | None:
        settings = get_settings()
        if not settings.langgraph_store_enabled or self._disabled_for_event_loop:
            return None
        if self._store is not None:
            return self._store
        async with self._lock:
            if self._disabled_for_event_loop:
                return None
            if self._store is not None:
                return self._store
            if _uses_windows_proactor_loop():
                self._disabled_for_event_loop = True
                logger.warning(
                    "Disabling the LangGraph Postgres store because the current Windows "
                    "Proactor event loop is incompatible. Use voice_shopping_api.server."
                )
                return None
            index = None
            if settings.dashscope_api_key:
                index = {
                    "dims": settings.langgraph_store_embedding_dimensions,
                    "embed": _embed_texts,
                    "fields": ["text"],
                }
            try:
                async with asyncio.timeout(settings.langgraph_store_init_timeout_seconds):
                    store = await self._resources.enter_async_context(
                        AsyncPostgresStore.from_conn_string(
                            settings.langgraph_store_url,
                            index=index,
                        )
                    )
                    await store.setup()
            except TimeoutError:
                logger.warning(
                    "LangGraph Postgres store initialization timed out after %.1fs; "
                    "continuing without long-term memory",
                    settings.langgraph_store_init_timeout_seconds,
                )
                await self._disable_after_failure()
                return None
            except Exception:
                logger.exception(
                    "LangGraph Postgres store initialization failed; continuing without "
                    "long-term memory"
                )
                await self._disable_after_failure()
                return None
            self._store = store
            return store

    async def _disable_after_failure(self) -> None:
        self._disabled_for_event_loop = True
        with suppress(Exception):
            await self._resources.aclose()
        self._resources = AsyncExitStack()

    async def close(self) -> None:
        async with self._lock:
            await self._resources.aclose()
            self._resources = AsyncExitStack()
            self._store = None
            self._disabled_for_event_loop = False


_store = LazyPostgresStore()


async def get_memory_store() -> BaseStore | None:
    return await _store.get()


async def close_memory_store() -> None:
    await _store.close()
