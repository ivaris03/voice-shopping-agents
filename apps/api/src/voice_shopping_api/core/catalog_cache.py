"""Best-effort Redis cache for merchant and product collection responses."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from fastapi.encoders import jsonable_encoder
from redis.asyncio import Redis
from redis.exceptions import RedisError

from voice_shopping_api.core.config import get_settings

logger = logging.getLogger(__name__)

CatalogPayload = dict[str, object]
CatalogLoader = Callable[[], Awaitable[CatalogPayload]]


class CatalogCache:
    """Cache collection views while keeping PostgreSQL as the source of truth.

    All list keys include a shared revision. A successful merchant, store, or
    product write increments that revision, so callers stop using every prior
    collection snapshot without relying on Redis key scans.
    """

    _PREFIX = "voice-shopping:cache:catalog"
    _REVISION_KEY = f"{_PREFIX}:revision"

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        redis_client: Redis | None = None,
    ) -> None:
        settings = get_settings()
        self.enabled = settings.catalog_cache_enabled if enabled is None else enabled
        self._redis_url = settings.catalog_cache_redis_url or settings.redis_url
        self._ttl_seconds = settings.catalog_cache_ttl_seconds
        self._redis = redis_client
        self._owns_client = redis_client is None

    def _client(self) -> Redis:
        if self._redis is None:
            self._redis = Redis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    @staticmethod
    def _arguments_digest(arguments: Mapping[str, Any]) -> str:
        canonical = json.dumps(
            jsonable_encoder(arguments),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()[:24]

    def _value_key(self, revision: int, scope: str, arguments: Mapping[str, Any]) -> str:
        return f"{self._PREFIX}:v{revision}:{scope}:{self._arguments_digest(arguments)}"

    async def _revision(self) -> int | None:
        try:
            value = await self._client().get(self._REVISION_KEY)
        except (OSError, RedisError, TimeoutError):
            logger.debug("Catalog cache revision lookup failed", exc_info=True)
            return None
        if value is None:
            return 0
        try:
            return int(value)
        except (TypeError, ValueError):
            logger.warning("Ignoring invalid catalog cache revision")
            return 0

    async def get_or_load(
        self,
        scope: str,
        arguments: Mapping[str, Any],
        loader: CatalogLoader,
    ) -> CatalogPayload:
        if not self.enabled:
            return await loader()

        revision = await self._revision()
        if revision is None:
            return await loader()

        key = self._value_key(revision, scope, arguments)
        try:
            cached = await self._client().get(key)
            if cached is not None:
                value = json.loads(cached)
                if isinstance(value, dict) and isinstance(value.get("items"), list):
                    return value
        except (OSError, RedisError, TimeoutError, json.JSONDecodeError):
            logger.debug("Catalog cache lookup failed", exc_info=True)

        value = await loader()
        try:
            encoded = json.dumps(jsonable_encoder(value), ensure_ascii=False, separators=(",", ":"))
            await self._client().set(key, encoded, ex=self._ttl_seconds)
        except (OSError, RedisError, TimeoutError, TypeError, ValueError):
            logger.debug("Catalog cache write failed", exc_info=True)
        return value

    async def invalidate(self) -> None:
        """Make previous merchant/store/product list snapshots unreachable."""
        if not self.enabled:
            return
        try:
            await self._client().incr(self._REVISION_KEY)
        except (OSError, RedisError, TimeoutError):
            logger.warning("Catalog cache invalidation failed", exc_info=True)

    async def close(self) -> None:
        if self._redis is not None and self._owns_client:
            await self._redis.aclose()
        self._redis = None


catalog_cache = CatalogCache()


def get_catalog_cache() -> CatalogCache:
    return catalog_cache
