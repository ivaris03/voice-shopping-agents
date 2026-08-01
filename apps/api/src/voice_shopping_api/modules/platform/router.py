from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from voice_shopping_api.core.database import get_db_session
from voice_shopping_api.core.queries import (
    MERCHANT_COLUMNS,
    ORDER_COLUMNS,
    PRODUCT_COLUMNS,
    commit_or_conflict,
    row_or_404,
    rows,
)
from voice_shopping_api.core.taxonomy import list_categories
from voice_shopping_api.schemas.domain import (
    CategoryCreate,
    CategoryOut,
    CategoryUpdate,
    ItemsResponse,
    MerchantOut,
    MerchantStatusUpdate,
    OrderOut,
    ProductOut,
)

router = APIRouter()
Db = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("/categories", response_model=ItemsResponse[CategoryOut])
async def get_categories(session: Db) -> dict[str, object]:
    return {"items": await list_categories(session)}


@router.post("/categories", response_model=CategoryOut, status_code=201)
async def create_category(payload: CategoryCreate, session: Db) -> dict[str, Any]:
    result = await session.execute(
        text(
            """
            INSERT INTO categories (
                category_l1, category_l2, required_slots, optional_slots
            ) VALUES (
                :category_l1, :category_l2, :required_slots, :optional_slots
            )
            ON CONFLICT (category_l2) DO NOTHING
            RETURNING *
            """
        ),
        payload.model_dump(),
    )
    created = result.mappings().first()
    if created is None:
        raise HTTPException(status_code=409, detail="二级分类已存在")
    await session.commit()
    return dict(created)


@router.patch("/categories/{category_id}", response_model=CategoryOut)
async def update_category(
    category_id: UUID, payload: CategoryUpdate, session: Db
) -> dict[str, Any]:
    current_result = await session.execute(
        text("SELECT * FROM categories WHERE id = :id"), {"id": category_id}
    )
    current = row_or_404(current_result, "分类不存在")
    values = payload.model_dump(exclude_unset=True)
    required_slots = list(dict.fromkeys(values.get("required_slots", current["required_slots"])))
    optional_slots = list(dict.fromkeys(values.get("optional_slots", current["optional_slots"])))
    duplicated = set(required_slots) & set(optional_slots)
    if duplicated:
        raise HTTPException(
            status_code=422,
            detail=f"槽位不能同时为必填和选填：{'、'.join(sorted(duplicated))}",
        )
    values.update(required_slots=required_slots, optional_slots=optional_slots)
    assignments = []
    for key in values:
        expression = f"CAST(:{key} AS text[])" if key.endswith("_slots") else f":{key}"
        assignments.append(f"{key} = {expression}")
    result = await session.execute(
        text(f"UPDATE categories SET {', '.join(assignments)} WHERE id = :id RETURNING *"),
        {**values, "id": category_id},
    )
    updated = row_or_404(result, "分类不存在")
    await commit_or_conflict(session, "二级分类已存在")
    return updated


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(category_id: UUID, session: Db) -> Response:
    result = await session.execute(
        text(
            """
            DELETE FROM categories c
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
async def list_all_merchants(session: Db) -> dict[str, object]:
    result = await session.execute(
        text(
            f"""
            SELECT {MERCHANT_COLUMNS} FROM merchants m
            LEFT JOIN products p ON p.merchant_id = m.id AND p.deleted_at IS NULL
            WHERE m.deleted_at IS NULL GROUP BY m.id ORDER BY m.created_at
            """
        )
    )
    return {"items": rows(result)}


@router.patch("/merchants/{merchant_id}/status", response_model=MerchantOut)
async def set_merchant_status(
    merchant_id: UUID, payload: MerchantStatusUpdate, session: Db
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
    current = await session.execute(
        text(
            f"""
            SELECT {MERCHANT_COLUMNS} FROM merchants m
            LEFT JOIN products p ON p.merchant_id = m.id AND p.deleted_at IS NULL
            WHERE m.id = :id GROUP BY m.id
            """
        ),
        {"id": merchant_id},
    )
    return row_or_404(current, "商家不存在")


@router.get("/products", response_model=ItemsResponse[ProductOut])
async def list_all_products(session: Db) -> dict[str, object]:
    result = await session.execute(
        text(
            f"""
            SELECT {PRODUCT_COLUMNS} FROM products p JOIN merchants m ON m.id = p.merchant_id
            WHERE p.deleted_at IS NULL AND m.deleted_at IS NULL ORDER BY p.created_at DESC
            """
        )
    )
    return {"items": rows(result)}


@router.get("/orders", response_model=ItemsResponse[OrderOut])
async def list_all_orders(session: Db) -> dict[str, object]:
    result = await session.execute(
        text(f"SELECT {ORDER_COLUMNS} FROM orders o ORDER BY o.created_at DESC")
    )
    return {"items": rows(result)}
