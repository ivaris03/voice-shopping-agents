"""DashScope embedding 网关：查询文本与商品文本共用同一套向量化调用。"""
import json
import logging
from typing import Any

import httpx

from voice_shopping_api.core.config import get_settings

logger = logging.getLogger(__name__)


async def embed_text(text: str) -> tuple[list[float], dict[str, Any] | None]:
    """Call the configured embedding model for one text and return (vector, usage).

    The returned vector is the raw model output; callers that persist it must
    normalize it first (see ``normalize_embedding``).
    """
    settings = get_settings()
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{settings.dashscope_chat_base_url.rstrip('/')}/embeddings",
            headers={"Authorization": f"Bearer {settings.dashscope_api_key}"},
            json={
                "model": settings.embedding_model,
                "input": text,
                "dimensions": 1024,
                "encoding_format": "float",
            },
        )
        response.raise_for_status()
    response_data = response.json()
    vector = [float(value) for value in response_data["data"][0]["embedding"]]
    return vector, response_data.get("usage")


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
