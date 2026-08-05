import json
import logging
from typing import Any

from fastapi.encoders import jsonable_encoder
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from voice_shopping_api.core.config import get_settings

logger = logging.getLogger(__name__)

TAXONOMY_CACHE_PREFIX = "voice-shopping:cache:taxonomy:v1"
CATEGORY_LIST_CACHE_KEY = f"{TAXONOMY_CACHE_PREFIX}:categories"
CATEGORY_LEVEL_ONE_CACHE_KEY = f"{TAXONOMY_CACHE_PREFIX}:category-level-ones"


class TaxonomyCache:
    """Best-effort shared cache for read-mostly platform taxonomy data."""

    def __init__(self, redis: Redis | None = None, *, ttl_seconds: int | None = None) -> None:
        self._redis = redis
        self._ttl_seconds = ttl_seconds

    async def start(self) -> None:
        """Enable caching for the application process without requiring Redis at startup."""
        if self._redis is not None:
            return
        try:
            self._redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
        except (RedisError, ValueError):
            logger.warning("Taxonomy cache is disabled because Redis could not be configured")

    async def close(self) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.aclose()
        except RedisError:
            logger.warning("Unable to close taxonomy cache Redis client", exc_info=True)
        finally:
            self._redis = None

    async def get(self, key: str) -> list[dict[str, Any]] | None:
        if self._redis is None:
            return None
        try:
            value = await self._redis.get(key)
            if value is None:
                return None
            decoded = json.loads(value)
            if not isinstance(decoded, list) or not all(isinstance(item, dict) for item in decoded):
                raise ValueError("Taxonomy cache payload must be a list of objects")
            return decoded
        except (RedisError, TypeError, ValueError, json.JSONDecodeError):
            logger.warning("Unable to read taxonomy cache key %s", key, exc_info=True)
            await self.delete(key)
            return None

    async def set(self, key: str, value: list[dict[str, Any]]) -> None:
        if self._redis is None:
            return
        try:
            payload = json.dumps(
                jsonable_encoder(value), ensure_ascii=False, separators=(",", ":")
            )
            await self._redis.set(
                key,
                payload,
                ex=self._ttl_seconds or get_settings().taxonomy_cache_ttl_seconds,
            )
        except (RedisError, TypeError, ValueError):
            logger.warning("Unable to write taxonomy cache key %s", key, exc_info=True)

    async def delete(self, *keys: str) -> None:
        if self._redis is None or not keys:
            return
        try:
            await self._redis.delete(*keys)
        except RedisError:
            logger.warning("Unable to invalidate taxonomy cache", exc_info=True)


taxonomy_cache = TaxonomyCache()

REQUIRED_ATTRIBUTE_KEYS_BY_CATEGORY: dict[str, tuple[str, ...]] = {
    "HEADPHONES": (
        "noiseCancellation",
        "form",
        "connectivity",
        "batteryHours",
    ),
    "COFFEE_MACHINE": (
        "type",
        "steamWand",
        "pressureBar",
        "waterTankMl",
    ),
    "ELECTRIC_KETTLE": (
        "capacityL",
        "temperatureControl",
        "keepWarm",
    ),
    "RUNNING_SHOES": (
        "gender",
        "size",
        "terrain",
        "cushion",
        "footType",
    ),
    "WATCHES": (
        "gender",
        "movement",
        "material",
        "waterResistance",
    ),
    "LIPSTICK": (
        "shade",
        "finish",
        "skinType",
    ),
}

ATTRIBUTE_KEYS_BY_CATEGORY: dict[str, frozenset[str]] = {
    category: frozenset(keys) for category, keys in REQUIRED_ATTRIBUTE_KEYS_BY_CATEGORY.items()
}


def normalize_attributes(category_l2: str, attributes: dict[str, Any]) -> dict[str, Any]:
    expected = ATTRIBUTE_KEYS_BY_CATEGORY.get(category_l2)
    if expected is None:
        raise ValueError(f"不支持的二级品类：{category_l2}")
    unexpected = set(attributes) - expected
    if unexpected:
        names = "、".join(sorted(unexpected))
        raise ValueError(f"{category_l2} 包含未定义的 attributes：{names}")
    return {key: attributes.get(key) for key in sorted(expected)}


def validate_attributes(
    category_l2: str,
    attributes: dict[str, Any],
    slots: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate one product against the platform-maintained slot definition."""
    allowed_slots = {str(slot["key"]) for slot in slots}
    unexpected = set(attributes) - allowed_slots
    if unexpected:
        names = "、".join(sorted(unexpected))
        raise ValueError(f"{category_l2} 包含未定义的槽位：{names}")

    missing = [
        str(slot["key"])
        for slot in slots
        if slot["is_required"] and _is_empty(attributes.get(str(slot["key"])))
    ]
    if missing:
        raise ValueError(f"请填写必填槽位：{'、'.join(missing)}")

    invalid = []
    for slot in slots:
        key = str(slot["key"])
        value = attributes.get(key)
        if _is_empty(value):
            continue
        enum_values = slot["enum_values"]
        if isinstance(value, list):
            is_valid = bool(value) and all(item in enum_values for item in value)
        else:
            is_valid = value in enum_values
        if not is_valid:
            invalid.append(key)
    if invalid:
        raise ValueError(f"槽位值不在平台枚举范围内：{'、'.join(invalid)}")

    return {key: value for key, value in attributes.items() if not _is_empty(value)}


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


async def validate_product_taxonomy(
    session: AsyncSession,
    category_l1: str,
    category_l2: str,
    attributes: dict[str, Any],
) -> dict[str, Any]:
    category = next(
        (
            item
            for item in await list_categories(session)
            if str(item["category_l2"]) == category_l2
        ),
        None,
    )
    if category is None:
        raise ValueError(f"二级品类不存在：{category_l2}")
    if str(category["category_l1"]) != category_l1:
        raise ValueError("所选二级品类不属于该一级品类")
    return validate_attributes(category_l2, attributes, list(category["slots"]))


async def list_categories(
    session: AsyncSession, *, force_refresh: bool = False
) -> list[dict[str, Any]]:
    if not force_refresh:
        cached = await taxonomy_cache.get(CATEGORY_LIST_CACHE_KEY)
        if cached is not None:
            return cached

    categories = await _list_categories_from_database(session)
    await taxonomy_cache.set(CATEGORY_LIST_CACHE_KEY, categories)
    return categories


async def _list_categories_from_database(session: AsyncSession) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT c.id, c.category_l1_id, g.code AS category_l1, c.category_l2,
                   COALESCE(
                       array_agg(s.key ORDER BY s.created_at, s.key)
                           FILTER (WHERE s.is_required), ARRAY[]::text[]
                   ) AS required_slots,
                   COALESCE(
                       array_agg(s.key ORDER BY s.created_at, s.key)
                           FILTER (WHERE NOT s.is_required), ARRAY[]::text[]
                   ) AS optional_slots,
                   COALESCE(
                       jsonb_agg(
                           jsonb_build_object(
                               'id', s.id,
                               'key', s.key,
                               'is_required', s.is_required,
                               'enum_values', s.enum_values
                           ) ORDER BY s.created_at, s.key
                       ) FILTER (WHERE s.id IS NOT NULL), '[]'::jsonb
                   ) AS slots,
                   c.created_at, c.updated_at
            FROM category_l2 c
            JOIN category_l1 g ON g.id = c.category_l1_id
            LEFT JOIN category_slots s ON s.category_id = c.id
            GROUP BY c.id, c.category_l1_id, g.code, c.category_l2, c.created_at, c.updated_at
            ORDER BY g.code, c.category_l2, c.created_at
            """
        )
    )
    return [dict(row) for row in result.mappings().all()]


async def list_category_level_ones(session: AsyncSession) -> list[dict[str, Any]]:
    cached = await taxonomy_cache.get(CATEGORY_LEVEL_ONE_CACHE_KEY)
    if cached is not None:
        return cached

    result = await session.execute(text("SELECT * FROM category_l1 ORDER BY code, created_at"))
    categories = [dict(row) for row in result.mappings().all()]
    await taxonomy_cache.set(CATEGORY_LEVEL_ONE_CACHE_KEY, categories)
    return categories


async def invalidate_taxonomy_cache() -> None:
    """Discard taxonomy snapshots after their source transaction has committed."""
    await taxonomy_cache.delete(CATEGORY_LIST_CACHE_KEY, CATEGORY_LEVEL_ONE_CACHE_KEY)


async def start_taxonomy_cache() -> None:
    await taxonomy_cache.start()


async def close_taxonomy_cache() -> None:
    await taxonomy_cache.close()
