"""DashScope embedding gateway used by queries and product indexing."""
import asyncio
import json
import logging
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


async def embed_product_text(text: str) -> str | None:
    """生成并归一化商品向量，返回可直接 CAST 为 vector 的 JSON 字符串。

    embedding 服务不可用时降级为 None（调用方写入 NULL），不阻断业务操作；
    召回侧对 NULL embedding 已有词法降级链路。
    """
    try:
        vector, _ = await embed_text(text)
    except Exception as exc:
        logger.warning("商品向量生成失败，embedding 降级为空: %s", exc)
        return None
    return json.dumps(normalize_embedding(vector))
