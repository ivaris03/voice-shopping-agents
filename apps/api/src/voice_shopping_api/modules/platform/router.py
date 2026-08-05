import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from voice_shopping_api.core.catalog_cache import CatalogCache, get_catalog_cache
from voice_shopping_api.core.database import get_db_session
from voice_shopping_api.core.embeddings import embed_product_text
from voice_shopping_api.core.product_embedding import embedding_text_for_product
from voice_shopping_api.core.queries import (
    ORDER_COLUMNS,
    PLATFORM_MERCHANT_COLUMNS,
    PRODUCT_COLUMNS,
    commit_or_conflict,
    row_or_404,
    rows,
)
from voice_shopping_api.core.taxonomy import list_categories
from voice_shopping_api.schemas.domain import (
    CategoryCreate,
    CategoryL1Create,
    CategoryL1Out,
    CategoryOut,
    CategorySlotCreate,
    CategorySlotOut,
    CategorySlotUpdate,
    CategoryUpdate,
    ItemsResponse,
    MerchantOut,
    MerchantStatusUpdate,
    OrderOut,
    ProductOut,
)

router = APIRouter()
Db = Annotated[AsyncSession, Depends(get_db_session)]
Cache = Annotated[CatalogCache, Depends(get_catalog_cache)]


@router.get("/categories", response_model=ItemsResponse[CategoryOut])
async def get_categories(session: Db) -> dict[str, object]:
    return {"items": await list_categories(session)}


async def _category_or_404(session: AsyncSession, category_id: UUID) -> dict[str, Any]:
    for category in await list_categories(session):
        if category["id"] == category_id:
            return category
    raise HTTPException(status_code=404, detail="二级分类不存在")


@router.get("/category-level-ones", response_model=ItemsResponse[CategoryL1Out])
async def get_category_level_ones(session: Db) -> dict[str, object]:
    result = await session.execute(text("SELECT * FROM category_l1 ORDER BY code, created_at"))
    return {"items": rows(result)}


@router.post("/category-level-ones", response_model=CategoryL1Out, status_code=201)
async def create_category_level_one(payload: CategoryL1Create, session: Db) -> dict[str, Any]:
    result = await session.execute(
        text(
            """
            INSERT INTO category_l1 (code) VALUES (:code)
            ON CONFLICT (code) DO NOTHING
            RETURNING *
            """
        ),
        payload.model_dump(),
    )
    created = result.mappings().first()
    if created is None:
        raise HTTPException(status_code=409, detail="一级分类已存在")
    await session.commit()
    return dict(created)


@router.delete("/category-level-ones/{category_l1_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category_level_one(category_l1_id: UUID, session: Db) -> Response:
    result = await session.execute(
        text(
            """
            DELETE FROM category_l1 g
            WHERE g.id = :id
              AND NOT EXISTS (
                  SELECT 1 FROM category_l2 c WHERE c.category_l1_id = g.id
              )
            RETURNING id
            """
        ),
        {"id": category_l1_id},
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=409, detail="一级分类不存在或仍有关联二级分类")
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/categories", response_model=CategoryOut, status_code=201)
async def create_category(payload: CategoryCreate, session: Db) -> dict[str, Any]:
    parent_result = await session.execute(
        text("SELECT code FROM category_l1 WHERE id = :id"),
        {"id": payload.category_l1_id},
    )
    category_l1 = parent_result.scalar_one_or_none()
    if category_l1 is None:
        raise HTTPException(status_code=422, detail="二级分类必须关联已存在的一级分类")
    result = await session.execute(
        text(
            """
            INSERT INTO category_l2 (
                category_l1_id, category_l1, category_l2
            ) VALUES (
                :category_l1_id, :category_l1, :category_l2
            )
            ON CONFLICT (category_l2) DO NOTHING
            RETURNING id
            """
        ),
        {**payload.model_dump(), "category_l1": category_l1},
    )
    category_id = result.scalar_one_or_none()
    if category_id is None:
        raise HTTPException(status_code=409, detail="二级分类已存在")
    await session.commit()
    return await _category_or_404(session, category_id)


@router.patch("/categories/{category_id}", response_model=CategoryOut)
async def update_category(
    category_id: UUID, payload: CategoryUpdate, session: Db
) -> dict[str, Any]:
    current_result = await session.execute(
        text("SELECT * FROM category_l2 WHERE id = :id"), {"id": category_id}
    )
    row_or_404(current_result, "二级分类不存在")
    values = payload.model_dump(exclude_unset=True)
    if "category_l1_id" in values:
        parent_result = await session.execute(
            text("SELECT code FROM category_l1 WHERE id = :id"),
            {"id": values["category_l1_id"]},
        )
        category_l1 = parent_result.scalar_one_or_none()
        if category_l1 is None:
            raise HTTPException(status_code=422, detail="二级分类必须关联已存在的一级分类")
        values["category_l1"] = category_l1
    if values:
        assignments = ", ".join(f"{key} = :{key}" for key in values)
        await session.execute(
            text(f"UPDATE category_l2 SET {assignments} WHERE id = :id"),
            {**values, "id": category_id},
        )
        await commit_or_conflict(session, "二级分类已存在")
    return await _category_or_404(session, category_id)


@router.post("/categories/{category_id}/slots", response_model=CategorySlotOut, status_code=201)
async def create_category_slot(
    category_id: UUID, payload: CategorySlotCreate, session: Db
) -> dict[str, Any]:
    category = await session.execute(
        text("SELECT id FROM category_l2 WHERE id = :id"), {"id": category_id}
    )
    if category.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="二级分类不存在")
    result = await session.execute(
        text(
            """
            INSERT INTO category_slots (category_id, key, is_required, enum_values)
            VALUES (:category_id, :key, :is_required, CAST(:enum_values AS jsonb))
            ON CONFLICT (category_id, key) DO NOTHING
            RETURNING *
            """
        ),
        {
            "category_id": category_id,
            "key": payload.key,
            "is_required": payload.is_required,
            "enum_values": json.dumps(payload.enum_values, ensure_ascii=False),
        },
    )
    created = result.mappings().first()
    if created is None:
        raise HTTPException(status_code=409, detail="该二级分类下的槽位已存在")
    await session.commit()
    return dict(created)


@router.patch("/category-slots/{slot_id}", response_model=CategorySlotOut)
async def update_category_slot(
    slot_id: UUID, payload: CategorySlotUpdate, session: Db
) -> dict[str, Any]:
    values = payload.model_dump(exclude_unset=True)
    if "enum_values" in values:
        values["enum_values"] = json.dumps(values["enum_values"], ensure_ascii=False)
    if values:
        assignments = ", ".join(
            f"{key} = CAST(:{key} AS jsonb)" if key == "enum_values" else f"{key} = :{key}"
            for key in values
        )
        result = await session.execute(
            text(f"UPDATE category_slots SET {assignments} WHERE id = :id RETURNING *"),
            {**values, "id": slot_id},
        )
        updated = row_or_404(result, "槽位不存在")
        await session.commit()
        return updated
    result = await session.execute(
        text("SELECT * FROM category_slots WHERE id = :id"), {"id": slot_id}
    )
    return row_or_404(result, "槽位不存在")


@router.delete("/category-slots/{slot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category_slot(slot_id: UUID, session: Db) -> Response:
    result = await session.execute(
        text("DELETE FROM category_slots WHERE id = :id RETURNING id"), {"id": slot_id}
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="槽位不存在")
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(category_id: UUID, session: Db) -> Response:
    result = await session.execute(
        text(
            """
            DELETE FROM category_l2 c
            WHERE c.id = :id
              AND NOT EXISTS (
                SELECT 1 FROM products p
                WHERE p.category_l2 = c.category_l2 AND p.deleted_at IS NULL
              )
            RETURNING id
            """
        ),
        {"id": category_id},
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=409, detail="分类不存在或仍有关联商品")
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/merchants", response_model=ItemsResponse[MerchantOut])
async def list_all_merchants(session: Db, cache: Cache) -> dict[str, object]:
    async def load() -> dict[str, object]:
        result = await session.execute(
            text(
                f"""
                SELECT {PLATFORM_MERCHANT_COLUMNS} FROM merchants m
                JOIN users u ON u.id = m.owner_user_id
                LEFT JOIN products p ON p.merchant_id = m.id AND p.deleted_at IS NULL
                WHERE m.deleted_at IS NULL GROUP BY m.id, u.display_name ORDER BY m.created_at
                """
            )
        )
        return {"items": rows(result)}

    return await cache.get_or_load("all-merchants", {}, load)


@router.patch("/merchants/{merchant_id}/status", response_model=MerchantOut)
async def set_merchant_status(
    merchant_id: UUID, payload: MerchantStatusUpdate, session: Db, cache: Cache
) -> dict[str, object]:
    result = await session.execute(
        text(
            """
            UPDATE merchants SET is_enabled = :enabled,
                disabled_reason = CASE WHEN :enabled THEN NULL ELSE :reason END
            WHERE id = :id AND deleted_at IS NULL RETURNING id
            """
        ),
        {"id": merchant_id, "enabled": payload.is_enabled, "reason": payload.disabled_reason},
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="商家不存在")
    await session.commit()
    await cache.invalidate()
    current = await session.execute(
        text(
            f"""
            SELECT {PLATFORM_MERCHANT_COLUMNS} FROM merchants m
            JOIN users u ON u.id = m.owner_user_id
            LEFT JOIN products p ON p.merchant_id = m.id AND p.deleted_at IS NULL
            WHERE m.id = :id GROUP BY m.id, u.display_name
            """
        ),
        {"id": merchant_id},
    )
    return row_or_404(current, "商家不存在")


@router.post("/products/embeddings/rebuild")
async def rebuild_product_embeddings(session: Db, cache: Cache) -> dict[str, int]:
    """为全部未删除商品重新生成向量，覆盖占位或过期的 embedding。

    同步执行：当前数据量下每个商品一次 embedding 调用，整体几秒内完成；
    商品数量增长后应改为后台任务，避免长时间占用请求连接。
    """
    result = await session.execute(
        text(
            """
            SELECT id, name, category_l1, category_l2, brand, description,
                   price, attributes, selling_points
            FROM products WHERE deleted_at IS NULL ORDER BY created_at
            """
        )
    )
    products = result.mappings().all()
    updated = 0
    failed = 0
    for product in products:
        wire = await embed_product_text(embedding_text_for_product(product))
        if wire is None:
            failed += 1
            continue
        await session.execute(
            text("UPDATE products SET embedding = CAST(:embedding AS vector) WHERE id = :id"),
            {"id": product["id"], "embedding": wire},
        )
        updated += 1
    await session.commit()
    if updated:
        await cache.invalidate()
    return {"total": len(products), "updated": updated, "failed": failed}


@router.get("/products", response_model=ItemsResponse[ProductOut])
async def list_all_products(session: Db, cache: Cache) -> dict[str, object]:
    async def load() -> dict[str, object]:
        result = await session.execute(
            text(
                f"""
                SELECT {PRODUCT_COLUMNS} FROM products p JOIN merchants m ON m.id = p.merchant_id
                WHERE p.deleted_at IS NULL AND m.deleted_at IS NULL ORDER BY p.created_at DESC
                """
            )
        )
        return {"items": rows(result)}

    return await cache.get_or_load("all-products", {}, load)


@router.get("/orders", response_model=ItemsResponse[OrderOut])
async def list_all_orders(session: Db) -> dict[str, object]:
    result = await session.execute(
        text(f"SELECT {ORDER_COLUMNS} FROM orders o ORDER BY o.created_at DESC")
    )
    return {"items": rows(result)}
