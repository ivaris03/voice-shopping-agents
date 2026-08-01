from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from voice_shopping_api.core.database import get_db_session
from voice_shopping_api.core.queries import (
    MERCHANT_COLUMNS,
    ORDER_COLUMNS,
    PRODUCT_COLUMNS,
    row_or_404,
    rows,
)
from voice_shopping_api.schemas.domain import (
    ItemsResponse,
    MerchantOut,
    MerchantStatusUpdate,
    OrderOut,
    ProductOut,
)

router = APIRouter()
Db = Annotated[AsyncSession, Depends(get_db_session)]


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
