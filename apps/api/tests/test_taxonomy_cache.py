from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from redis.exceptions import RedisError

from voice_shopping_api.core import taxonomy as taxonomy_module
from voice_shopping_api.modules.platform import router as platform_router
from voice_shopping_api.schemas.domain import CategoryL1Create

CATEGORY_ID = UUID("60000000-0000-4000-8000-000000000001")
CATEGORY_L1_ID = UUID("61000000-0000-4000-8000-000000000001")
SLOT_ID = UUID("62000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 8, 5, tzinfo=UTC)

CATEGORY = {
    "id": CATEGORY_ID,
    "category_l1_id": CATEGORY_L1_ID,
    "category_l1": "PETS",
    "category_l2": "CAT_FOOD",
    "required_slots": ["flavor"],
    "optional_slots": ["weightKg"],
    "slots": [
        {
            "id": SLOT_ID,
            "key": "flavor",
            "is_required": True,
            "enum_values": ["chicken", "fish"],
        },
        {
            "id": UUID("62000000-0000-4000-8000-000000000002"),
            "key": "weightKg",
            "is_required": False,
            "enum_values": [1, 2],
        },
    ],
    "created_at": NOW,
    "updated_at": NOW,
}

CATEGORY_LEVEL_ONE = {
    "id": CATEGORY_L1_ID,
    "code": "PETS",
    "created_at": NOW,
    "updated_at": NOW,
}


class MappingRows:
    def __init__(self, values: list[dict[str, Any]]) -> None:
        self.values = values

    def all(self) -> list[dict[str, Any]]:
        return self.values

    def first(self) -> dict[str, Any] | None:
        return self.values[0] if self.values else None


class Result:
    def __init__(self, values: list[dict[str, Any]]) -> None:
        self.values = values

    def mappings(self) -> MappingRows:
        return MappingRows(self.values)


class ReadSession:
    def __init__(self, values: list[dict[str, Any]]) -> None:
        self.values = values
        self.execute_calls = 0

    async def execute(self, *_args: Any, **_kwargs: Any) -> Result:
        self.execute_calls += 1
        return Result(self.values)


class NoDatabaseSession:
    async def execute(self, *_args: Any, **_kwargs: Any) -> Result:
        raise AssertionError("cache hit must not query the database")


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls: list[tuple[str, str, int]] = []
        self.delete_calls: list[tuple[str, ...]] = []

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ex: int) -> None:
        self.values[key] = value
        self.set_calls.append((key, value, ex))

    async def delete(self, *keys: str) -> None:
        self.delete_calls.append(keys)
        for key in keys:
            self.values.pop(key, None)


class FailingRedis(FakeRedis):
    async def get(self, _key: str) -> str | None:
        raise RedisError("Redis unavailable")


@pytest.fixture
def cache(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    redis = FakeRedis()
    monkeypatch.setattr(
        taxonomy_module,
        "taxonomy_cache",
        taxonomy_module.TaxonomyCache(redis, ttl_seconds=60),
    )
    return redis


@pytest.mark.asyncio
async def test_categories_and_slots_are_cached_and_reused_for_validation(cache: FakeRedis) -> None:
    session = ReadSession([CATEGORY])

    loaded = await taxonomy_module.list_categories(session)
    validated = await taxonomy_module.validate_product_taxonomy(
        NoDatabaseSession(),
        "PETS",
        "CAT_FOOD",
        {"flavor": "chicken"},
    )
    cached = await taxonomy_module.list_categories(NoDatabaseSession())

    assert loaded == [CATEGORY]
    assert validated == {"flavor": "chicken"}
    assert session.execute_calls == 1
    assert cache.set_calls[0][0] == taxonomy_module.CATEGORY_LIST_CACHE_KEY
    assert cache.set_calls[0][2] == 60
    assert cached == json.loads(cache.set_calls[0][1])


@pytest.mark.asyncio
async def test_force_refresh_replaces_a_stale_category_snapshot(cache: FakeRedis) -> None:
    await taxonomy_module.list_categories(ReadSession([CATEGORY]))
    refreshed_category = {**CATEGORY, "category_l2": "DOG_FOOD"}
    refresh_session = ReadSession([refreshed_category])

    refreshed = await taxonomy_module.list_categories(refresh_session, force_refresh=True)
    cached = await taxonomy_module.list_categories(NoDatabaseSession())

    assert refreshed == [refreshed_category]
    assert refresh_session.execute_calls == 1
    assert cached[0]["category_l2"] == "DOG_FOOD"


@pytest.mark.asyncio
async def test_category_level_ones_are_cached(cache: FakeRedis) -> None:
    session = ReadSession([CATEGORY_LEVEL_ONE])

    first = await taxonomy_module.list_category_level_ones(session)
    second = await taxonomy_module.list_category_level_ones(NoDatabaseSession())

    assert first == [CATEGORY_LEVEL_ONE]
    assert second == json.loads(cache.set_calls[0][1])
    assert session.execute_calls == 1
    assert cache.set_calls[0][0] == taxonomy_module.CATEGORY_LEVEL_ONE_CACHE_KEY


@pytest.mark.asyncio
async def test_invalidating_taxonomy_removes_both_snapshots(cache: FakeRedis) -> None:
    await taxonomy_module.taxonomy_cache.set(
        taxonomy_module.CATEGORY_LIST_CACHE_KEY,
        [CATEGORY],
    )
    await taxonomy_module.taxonomy_cache.set(
        taxonomy_module.CATEGORY_LEVEL_ONE_CACHE_KEY,
        [CATEGORY_LEVEL_ONE],
    )

    await taxonomy_module.invalidate_taxonomy_cache()

    assert cache.delete_calls == [
        (
            taxonomy_module.CATEGORY_LIST_CACHE_KEY,
            taxonomy_module.CATEGORY_LEVEL_ONE_CACHE_KEY,
        )
    ]
    assert cache.values == {}


@pytest.mark.asyncio
async def test_redis_read_failure_falls_back_to_the_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        taxonomy_module,
        "taxonomy_cache",
        taxonomy_module.TaxonomyCache(FailingRedis(), ttl_seconds=60),
    )
    session = ReadSession([CATEGORY])

    result = await taxonomy_module.list_categories(session)

    assert result == [CATEGORY]
    assert session.execute_calls == 1


@pytest.mark.asyncio
async def test_taxonomy_cache_is_invalidated_after_category_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class WriteSession:
        async def execute(self, *_args: Any, **_kwargs: Any) -> Result:
            return Result([CATEGORY_LEVEL_ONE])

        async def commit(self) -> None:
            events.append("commit")

    async def invalidate() -> None:
        events.append("invalidate")

    monkeypatch.setattr(platform_router, "invalidate_taxonomy_cache", invalidate)

    result = await platform_router.create_category_level_one(
        CategoryL1Create(code="PETS"),
        WriteSession(),
    )

    assert result == CATEGORY_LEVEL_ONE
    assert events == ["commit", "invalidate"]
