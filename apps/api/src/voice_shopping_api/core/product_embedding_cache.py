"""Redis-backed cache for normalized product embedding vectors."""

from __future__ import annotations

import hashlib
import json
import logging
import math

from redis.asyncio import Redis
from redis.exceptions import RedisError

from voice_shopping_api.core.config import get_settings

logger = logging.getLogger(__name__)

PRODUCT_EMBEDDING_CACHE_PREFIX = "voice-shopping:cache:product-embedding:v1"


def product_embedding_cache_key(text: str, model: str) -> str:
    """Build a stable key that changes with either model or indexed content."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"{PRODUCT_EMBEDDING_CACHE_PREFIX}:{model}:{digest}"


def _is_embedding_wire(value: str) -> bool:
    """Reject corrupt cache values before passing them to PostgreSQL's vector cast."""
    try:
        vector = json.loads(value)
    except (TypeError, ValueError):
        return False
    if not isinstance(vector, list) or not vector:
        return False
    for item in vector:
        if not isinstance(item, (int, float)) or isinstance(item, bool):
            return False
        try:
            if not math.isfinite(float(item)):
                return False
        except (OverflowError, TypeError, ValueError):
            return False
    return True


class ProductEmbeddingCache:
    """Fail-open cache shared by product create, update, and rebuild paths."""

    def __init__(
        self,
        redis: Redis | None = None,
        *,
        ttl_seconds: int | None = None,
    ) -> None:
        self._redis = redis
        self._ttl_seconds = ttl_seconds

    def _client(self) -> Redis:
        if self._redis is None:
            self._redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
        return self._redis

    @property
    def ttl_seconds(self) -> int:
        if self._ttl_seconds is not None:
            return self._ttl_seconds
        return get_settings().product_embedding_cache_ttl_seconds

    async def get(self, text: str, model: str) -> str | None:
        try:
            value = await self._client().get(product_embedding_cache_key(text, model))
        except RedisError:
            logger.warning("商品向量缓存读取失败，将调用 Embedding 模型", exc_info=True)
            return None
        if isinstance(value, str) and _is_embedding_wire(value):
            return value
        if value is not None:
            logger.warning("商品向量缓存值无效，将调用 Embedding 模型")
        return None

    async def set(self, text: str, model: str, wire: str) -> None:
        if not _is_embedding_wire(wire):
            logger.warning("商品向量结果无效，跳过缓存写入")
            return
        try:
            await self._client().set(
                product_embedding_cache_key(text, model),
                wire,
                ex=self.ttl_seconds,
            )
        except RedisError:
            logger.warning("商品向量缓存写入失败，将继续使用本次生成的向量", exc_info=True)

    async def close(self) -> None:
        if self._redis is None:
            return
        redis = self._redis
        self._redis = None
        await redis.aclose()


product_embedding_cache = ProductEmbeddingCache()
