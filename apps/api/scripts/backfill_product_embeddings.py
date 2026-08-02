"""重新为全部未删除商品生成向量（覆盖 demo 数据的 one-hot 占位向量）。

用法（在 apps/api 目录下）：
    uv run python scripts/backfill_product_embeddings.py

逐行拼装商品卡片 -> 调用 DashScope embedding -> 归一化后写回 embedding 列。
单个商品失败只记录日志并继续，结束时汇总失败数量。
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from voice_shopping_api.core.config import get_settings  # noqa: E402
from voice_shopping_api.core.embeddings import embed_product_text  # noqa: E402
from voice_shopping_api.core.product_embedding import embedding_text_for_product  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("backfill-product-embeddings")


async def main() -> None:
    settings = get_settings()
    if not settings.dashscope_api_key:
        raise SystemExit("DASHSCOPE_API_KEY 未配置，无法生成商品向量")
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                """
                SELECT id, name, category_l1, category_l2, brand, description, price,
                       attributes, selling_points
                FROM products
                WHERE deleted_at IS NULL
                ORDER BY created_at
                """
            )
        )
        products = result.mappings().all()

    failed = 0
    for index, product in enumerate(products, start=1):
        wire = await embed_product_text(embedding_text_for_product(product))
        if wire is None:
            logger.warning("商品 %s 向量生成失败", product["id"])
            failed += 1
            continue
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE products SET embedding = CAST(:embedding AS vector) WHERE id = :id"),
                {"id": product["id"], "embedding": wire},
            )
        if index % 10 == 0 or index == len(products):
            logger.info("进度 %d/%d", index, len(products))
    logger.info("完成：共 %d 个商品，失败 %d 个", len(products), failed)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
