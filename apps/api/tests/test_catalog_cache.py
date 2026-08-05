from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from redis.exceptions import ConnectionError

from voice_shopping_api.core.catalog_cache import CatalogCache
from voice_shopping_api.modules.catalog import router as catalog_router
from voice_shopping_api.modules.merchant.router import delete_product
from voice_shopping_api.modules.orders import router as orders_router


class MemoryRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ex: int) -> None:
        assert ex > 0
        self.values[key] = value

    async def incr(self, key: str) -> int:
        value = int(self.values.get(key, "0")) + 1
        self.values[key] = str(value)
        return value


class UnavailableRedis:
    async def get(self, _: str) -> str | None:
        raise ConnectionError("unavailable")


class CacheSpy:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def invalidate(self) -> None:
        self.events.append("invalidate")


class DeleteResult:
    def scalar_one_or_none(self) -> str:
        return "product-id"


class CommitSession:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def execute(self, _: Any, __: Mapping[str, Any]) -> DeleteResult:
        self.events.append("execute")
        return DeleteResult()

    async def commit(self) -> None:
        self.events.append("commit")


class RowsResult:
    def __init__(self, values: list[dict[str, object]]) -> None:
        self.values = values

    def mappings(self) -> RowsResult:
        return self

    def all(self) -> list[dict[str, object]]:
        return self.values


class QuerySession:
    def __init__(self) -> None:
        self.executions = 0

    async def execute(self, _: Any, __: Mapping[str, Any]) -> RowsResult:
        self.executions += 1
        return RowsResult([{"id": "product-1"}])


@pytest.mark.asyncio
async def test_catalog_cache_reuses_a_collection_until_its_revision_changes() -> None:
    redis = MemoryRedis()
    cache = CatalogCache(redis_client=redis)  # type: ignore[arg-type]
    calls = 0

    async def load() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"items": [{"id": f"product-{calls}"}]}

    first = await cache.get_or_load("visible-products", {"category": "HEADPHONES"}, load)
    second = await cache.get_or_load("visible-products", {"category": "HEADPHONES"}, load)

    assert first == second == {"items": [{"id": "product-1"}]}
    assert calls == 1

    await cache.invalidate()
    third = await cache.get_or_load("visible-products", {"category": "HEADPHONES"}, load)

    assert third == {"items": [{"id": "product-2"}]}
    assert calls == 2


@pytest.mark.asyncio
async def test_catalog_cache_bypasses_redis_when_it_is_unavailable() -> None:
    cache = CatalogCache(redis_client=UnavailableRedis())  # type: ignore[arg-type]
    calls = 0

    async def load() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"items": []}

    assert await cache.get_or_load("visible-merchants", {}, load) == {"items": []}
    assert calls == 1


@pytest.mark.asyncio
async def test_visible_product_route_reuses_the_cached_filter_result() -> None:
    session = QuerySession()
    cache = CatalogCache(redis_client=MemoryRedis())  # type: ignore[arg-type]

    first = await catalog_router.list_visible_products(
        session,  # type: ignore[arg-type]
        cache,
        category="HEADPHONES",
        query="Sony",
    )
    second = await catalog_router.list_visible_products(
        session,  # type: ignore[arg-type]
        cache,
        category="HEADPHONES",
        query="Sony",
    )

    assert first == second == {"items": [{"id": "product-1"}]}
    assert session.executions == 1


@pytest.mark.asyncio
async def test_product_delete_invalidates_after_the_database_commit() -> None:
    events: list[str] = []
    session = CommitSession(events)

    response = await delete_product(
        "20000000-0000-4000-8000-000000000001",  # type: ignore[arg-type]
        session,  # type: ignore[arg-type]
        "00000000-0000-4000-8000-000000000002",  # type: ignore[arg-type]
        CacheSpy(events),  # type: ignore[arg-type]
    )

    assert response.status_code == 204
    assert events == ["execute", "commit", "invalidate"]


@pytest.mark.asyncio
async def test_successful_order_confirmation_invalidates_after_the_database_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    session = CommitSession(events)

    async def confirm_order(*_: object) -> dict[str, object]:
        return {"status": "success", "session_id": None}

    monkeypatch.setattr(orders_router, "confirm_order", confirm_order)

    result = await orders_router.confirm(
        "30000000-0000-4000-8000-000000000001",  # type: ignore[arg-type]
        session,  # type: ignore[arg-type]
        "00000000-0000-4000-8000-000000000101",  # type: ignore[arg-type]
        CacheSpy(events),  # type: ignore[arg-type]
    )

    assert result["status"] == "success"
    assert events == ["commit", "invalidate"]
