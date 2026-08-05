import json
from collections.abc import Sequence
from typing import Any

import pytest

from voice_shopping_api.core import embeddings as embeddings_module
from voice_shopping_api.core.product_embedding_cache import (
    ProductEmbeddingCache,
    product_embedding_cache_key,
)
from voice_shopping_api.modules.platform import router as platform_router


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls: list[tuple[str, str, int | None]] = []

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        self.values[key] = value
        self.set_calls.append((key, value, ex))

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_product_embedding_cache_isolated_by_model_and_card_fingerprint() -> None:
    redis = _FakeRedis()
    cache = ProductEmbeddingCache(redis, ttl_seconds=90)
    card = "商品：通勤耳机；品类：数码电子-耳机"
    wire = "[0.6,0.8]"

    await cache.set(card, "embedding-v1", wire)

    assert await cache.get(card, "embedding-v1") == wire
    assert await cache.get(card, "embedding-v2") is None
    assert await cache.get(card + "；品牌：Sony", "embedding-v1") is None
    assert redis.set_calls == [(product_embedding_cache_key(card, "embedding-v1"), wire, 90)]


@pytest.mark.asyncio
async def test_product_embedding_resolution_reuses_cached_vector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = ProductEmbeddingCache(_FakeRedis(), ttl_seconds=90)
    calls: list[str] = []

    async def fake_embed_text(text: str) -> tuple[list[float], None]:
        calls.append(text)
        return [3.0, 4.0], None

    monkeypatch.setattr(embeddings_module, "product_embedding_cache", cache)
    monkeypatch.setattr(embeddings_module, "embed_text", fake_embed_text)

    first = await embeddings_module.resolve_product_embedding("商品：通勤耳机")
    second = await embeddings_module.resolve_product_embedding("商品：通勤耳机")

    assert first is not None
    assert first.cache_hit is False
    assert json.loads(first.wire) == pytest.approx([0.6, 0.8])
    assert second is not None
    assert second.wire == first.wire
    assert second.cache_hit is True
    assert calls == ["商品：通勤耳机"]


class _MappingsResult:
    def __init__(self, records: Sequence[dict[str, Any]]) -> None:
        self.records = records

    def mappings(self) -> "_MappingsResult":
        return self

    def all(self) -> Sequence[dict[str, Any]]:
        return self.records


class _RebuildSession:
    def __init__(self, products: Sequence[dict[str, Any]]) -> None:
        self.products = products
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.committed = False

    async def execute(
        self, statement: Any, params: dict[str, Any] | None = None
    ) -> _MappingsResult:
        sql = str(statement)
        self.calls.append((sql, params))
        if "SELECT id, name" in sql:
            return _MappingsResult(self.products)
        return _MappingsResult([])

    async def commit(self) -> None:
        self.committed = True


@pytest.mark.asyncio
async def test_rebuild_reports_cached_and_new_embeddings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    products = [
        {
            "id": "product-1",
            "name": "商品一",
            "category_l1": "ELECTRONICS",
            "category_l2": "HEADPHONES",
            "brand": None,
            "description": "",
            "price": 299,
            "attributes": {},
            "selling_points": [],
        },
        {
            "id": "product-2",
            "name": "商品二",
            "category_l1": "ELECTRONICS",
            "category_l2": "HEADPHONES",
            "brand": None,
            "description": "",
            "price": 399,
            "attributes": {},
            "selling_points": [],
        },
        {
            "id": "product-3",
            "name": "商品三",
            "category_l1": "ELECTRONICS",
            "category_l2": "HEADPHONES",
            "brand": None,
            "description": "",
            "price": 499,
            "attributes": {},
            "selling_points": [],
        },
    ]
    results = iter(
        [
            embeddings_module.ProductEmbeddingResult("[0.6,0.8]", cache_hit=True),
            embeddings_module.ProductEmbeddingResult("[0.8,0.6]", cache_hit=False),
            None,
        ]
    )

    async def resolve(_: str) -> embeddings_module.ProductEmbeddingResult | None:
        return next(results)

    monkeypatch.setattr(platform_router, "resolve_product_embedding", resolve)
    session = _RebuildSession(products)

    result = await platform_router.rebuild_product_embeddings(session)  # type: ignore[arg-type]

    assert result == {
        "total": 3,
        "updated": 2,
        "cacheHits": 1,
        "generated": 1,
        "failed": 1,
    }
    assert session.committed is True
    updates = [params for sql, params in session.calls if "UPDATE products SET embedding" in sql]
    assert updates == [
        {"id": "product-1", "embedding": "[0.6,0.8]"},
        {"id": "product-2", "embedding": "[0.8,0.6]"},
    ]
