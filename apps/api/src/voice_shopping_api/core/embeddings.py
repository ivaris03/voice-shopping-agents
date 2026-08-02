"""DashScope embedding gateway used by queries and product indexing."""
import asyncio
import json
import logging
from typing import Any

import dashscope
from langchain_community.embeddings import DashScopeEmbeddings

from voice_shopping_api.core.config import get_settings

logger = logging.getLogger(__name__)


async def embed_text(text: str) -> tuple[list[float], dict[str, Any] | None]:
    """Call the configured LangChain embedding model for one text.

    The returned vector is the raw model output; callers that persist it must
    normalize it first (see ``normalize_embedding``).
    """
    settings = get_settings()
    if not settings.dashscope_api_key:
        raise RuntimeError("DashScope API key is not configured")

    dashscope.api_key = settings.dashscope_api_key
    dashscope.base_http_api_url = settings.dashscope_http_base_url
    embeddings = DashScopeEmbeddings(
        model=settings.embedding_model,
        dashscope_api_key=settings.dashscope_api_key,
    )
    vector = await asyncio.to_thread(embeddings.embed_query, text)
    return [float(value) for value in vector], None


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
