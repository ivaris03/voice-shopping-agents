from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

MERCHANT_COLUMNS = """
    m.id, m.owner_user_id, m.name, m.slug, m.description, m.logo_url,
    m.contact_phone, m.is_enabled, m.disabled_reason, m.created_at, m.updated_at,
    count(p.id)::int AS product_count
"""

PRODUCT_COLUMNS = """
    p.id, p.merchant_id, m.name AS merchant_name, p.sku, p.name, p.category_l1,
    p.category_l2, p.brand, p.description, p.price, p.stock, p.attributes,
    p.selling_points, p.image_urls, p.status, p.created_at, p.updated_at
"""

ORDER_COLUMNS = """
    o.id, o.user_id, o.merchant_id, o.product_id, o.session_id, o.source_turn_id,
    o.status, o.quantity,
    o.unit_price, o.total_amount, o.merchant_snapshot, o.product_snapshot,
    o.failure_reason, o.expires_at, o.confirmed_at, o.created_at, o.updated_at
"""


def rows(result: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in result.mappings().all()]


def row_or_404(result: Any, detail: str) -> dict[str, Any]:
    value: Mapping[str, Any] | None = result.mappings().first()
    if value is None:
        raise HTTPException(status_code=404, detail=detail)
    return dict(value)


async def commit_or_conflict(session: AsyncSession, detail: str = "数据冲突") -> None:
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail=detail) from exc


async def owned_merchant_exists(
    session: AsyncSession, merchant_id: object, owner_id: object
) -> bool:
    result = await session.execute(
        text(
            """
            SELECT EXISTS(
                SELECT 1 FROM merchants
                WHERE id = :merchant_id AND owner_user_id = :owner_id AND deleted_at IS NULL
            )
            """
        ),
        {"merchant_id": merchant_id, "owner_id": owner_id},
    )
    return bool(result.scalar())
