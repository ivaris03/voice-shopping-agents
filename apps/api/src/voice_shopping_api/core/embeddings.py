"""DashScope embedding gateway used by queries and product indexing."""

import asyncio
import json
import logging
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import dashscope
from langchain_community.embeddings import DashScopeEmbeddings

from voice_shopping_api.core.config import get_settings
from voice_shopping_api.core.observability import (
    finish_trace,
    response_request_id,
    response_usage,
    start_trace,
)
from voice_shopping_api.core.product_embedding_cache import product_embedding_cache

logger = logging.getLogger(__name__)


class _RecordingEmbeddingClient:
    """Keep the SDK response for usage metadata while retaining LangChain's adapter."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.response: Any = None

    def call(self, **kwargs: Any) -> Any:
        self.response = self.client.call(**kwargs)
        return self.response


async def embed_text(text: str) -> tuple[list[float], dict[str, Any] | None]:
    """Call the configured LangChain embedding model for one text.

    The returned vector is the raw model output; callers that persist it must
    normalize it first (see ``normalize_embedding``).
    """
    settings = get_settings()
    started = perf_counter()
    span = start_trace(
        "dashscope-embedding",
        run_type="embedding",
        inputs={"text": text},
        metadata={
            "ls_provider": "dashscope",
            "ls_model_name": settings.embedding_model,
            "operation": "embedding",
            "text": text,
        },
        tags=["dashscope", "embedding"],
        project_name=settings.langsmith_project,
    )
    try:
        if not settings.dashscope_api_key:
            raise RuntimeError("DashScope API key is not configured")

        dashscope.api_key = settings.dashscope_api_key
        dashscope.base_http_api_url = settings.dashscope_http_base_url
        embeddings = DashScopeEmbeddings(
            model=settings.embedding_model,
            dashscope_api_key=settings.dashscope_api_key,
        )
        recorder = _RecordingEmbeddingClient(dashscope.TextEmbedding)
        embeddings.client = recorder
        vector = await asyncio.to_thread(embeddings.embed_query, text)
        vector = [float(value) for value in vector]
        usage = response_usage(recorder.response)
        finish_trace(
            span,
            outputs={"vector": vector, "vector_dimensions": len(vector)},
            metadata={
                "status": "ok",
                "duration_ms": round((perf_counter() - started) * 1000, 2),
                "text": text,
                "vector": vector,
                "vector_dimensions": len(vector),
                "request_id": response_request_id(recorder.response),
            },
            usage=usage,
        )
        return vector, usage
    except Exception as exc:
        finish_trace(
            span,
            metadata={
                "status": "error",
                "duration_ms": round((perf_counter() - started) * 1000, 2),
            },
            error=exc,
        )
        raise


def normalize_embedding(vector: list[float]) -> list[float]:
    """Return a unit vector so stored embeddings stay cosine-comparable in HNSW."""
    norm = sum(value * value for value in vector) ** 0.5
    if norm == 0:
        return vector
    return [value / norm for value in vector]


@dataclass(frozen=True)
class ProductEmbeddingResult:
    wire: str
    cache_hit: bool


async def resolve_product_embedding(text: str) -> ProductEmbeddingResult | None:
    """Resolve a normalized product vector from cache or the embedding provider.

    The cache is keyed by the model and canonical product card text, so a
    meaningful product change or model switch cannot reuse a stale vector.
    """
    settings = get_settings()
    cached = await product_embedding_cache.get(text, settings.embedding_model)
    if cached is not None:
        return ProductEmbeddingResult(wire=cached, cache_hit=True)
    try:
        vector, _ = await embed_text(text)
    except Exception as exc:
        logger.warning("商品向量生成失败，embedding 降级为空: %s", exc)
        return None
    wire = json.dumps(normalize_embedding(vector), separators=(",", ":"))
    await product_embedding_cache.set(text, settings.embedding_model, wire)
    return ProductEmbeddingResult(wire=wire, cache_hit=False)


async def embed_product_text(text: str) -> str | None:
    """Return a cached or newly generated product vector for existing callers."""
    result = await resolve_product_embedding(text)
    return result.wire if result is not None else None


async def close_product_embedding_cache() -> None:
    await product_embedding_cache.close()
