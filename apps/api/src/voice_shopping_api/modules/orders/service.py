from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from voice_shopping_api.core.queries import ORDER_COLUMNS, row_or_404
from voice_shopping_api.modules.catalog.profile import update_profiles
from voice_shopping_api.schemas.domain import OrderCreate


async def create_pending_order(
    session: AsyncSession, user_id: UUID, payload: OrderCreate
) -> dict[str, object]:
    result = await session.execute(
        text(
            f"""
            INSERT INTO orders (
                user_id, merchant_id, product_id, session_id, source_turn_id,
                idempotency_key, status, quantity, unit_price,
                merchant_snapshot, product_snapshot, expires_at
            )
            SELECT
                :user_id, m.id, p.id, :session_id, :source_turn_id,
                :idempotency_key, 'pending', :quantity, p.price,
                jsonb_build_object('merchantId', m.id, 'name', m.name),
                jsonb_build_object(
                    'productId', p.id, 'sku', p.sku, 'name', p.name,
                    'categoryL1', p.category_l1, 'categoryL2', p.category_l2,
                    'imageUrl', p.image_urls[1]
                ),
                CURRENT_TIMESTAMP + interval '15 minutes'
            FROM products p
            JOIN merchants m ON m.id = p.merchant_id
            WHERE p.id = :product_id
              AND p.deleted_at IS NULL AND p.status = 'on_sale' AND p.stock >= :quantity
              AND m.deleted_at IS NULL AND m.is_enabled
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING {ORDER_COLUMNS.replace("o.", "")}
            """
        ),
        {
            "user_id": user_id,
            "session_id": payload.session_id,
            "source_turn_id": payload.source_turn_id,
            "idempotency_key": payload.idempotency_key,
            "quantity": payload.quantity,
            "product_id": payload.product_id,
        },
    )
    created_row = result.mappings().first()
    if created_row is not None:
        return dict(created_row)

    # A conflicting insert can finish between the insert and this lookup. The
    # unique constraint is the serialization point, so a retry returns its
    # original order instead of turning a duplicate click into a second order.
    existing = await session.execute(
        text(f"SELECT {ORDER_COLUMNS} FROM orders o WHERE o.idempotency_key = :key"),
        {"key": payload.idempotency_key},
    )
    existing_row = existing.mappings().first()
    if existing_row is None:
        raise HTTPException(status_code=404, detail="商品不可售或库存不足")
    if existing_row["user_id"] != user_id:
        raise HTTPException(status_code=409, detail="幂等键已被占用")
    return dict(existing_row)


async def confirm_order(session: AsyncSession, user_id: UUID, order_id: UUID) -> dict[str, object]:
    result = await session.execute(
        text(f"SELECT {ORDER_COLUMNS} FROM orders o WHERE o.id = :id FOR UPDATE"),
        {"id": order_id},
    )
    order = row_or_404(result, "订单不存在")
    if order["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order["status"] != "pending":
        return order

    validation = await session.execute(
        text(
            """
            SELECT p.price, p.stock, p.status, p.deleted_at,
                   m.is_enabled, m.deleted_at AS merchant_deleted_at
            FROM products p JOIN merchants m ON m.id = p.merchant_id
            WHERE p.id = :product_id AND p.merchant_id = :merchant_id
            FOR UPDATE OF p, m
            """
        ),
        {"product_id": order["product_id"], "merchant_id": order["merchant_id"]},
    )
    product = validation.mappings().first()
    failure: str | None = None
    if order["expires_at"] <= datetime.now(UTC):
        failure = "confirmation_timeout"
    elif (
        product is None
        or product["deleted_at"]
        or product["merchant_deleted_at"]
        or not product["is_enabled"]
        or product["status"] != "on_sale"
    ):
        failure = "product_unavailable"
    elif product["price"] != order["unit_price"]:
        failure = "price_changed"
    elif product["stock"] < order["quantity"]:
        failure = "insufficient_stock"

    if failure:
        failed = await session.execute(
            text(
                f"""
                UPDATE orders SET status = 'fail', failure_reason = :reason
                WHERE id = :id RETURNING {ORDER_COLUMNS.replace("o.", "")}
                """
            ),
            {"id": order_id, "reason": failure},
        )
        return row_or_404(failed, "订单不存在")

    await session.execute(
        text("UPDATE products SET stock = stock - :quantity WHERE id = :product_id"),
        {"quantity": order["quantity"], "product_id": order["product_id"]},
    )
    confirmed = await session.execute(
        text(
            f"""
            UPDATE orders SET status = 'success', confirmed_at = CURRENT_TIMESTAMP
            WHERE id = :id RETURNING {ORDER_COLUMNS.replace("o.", "")}
            """
        ),
        {"id": order_id},
    )
    await update_profiles(session, user_id, order["product_id"], "order")
    return row_or_404(confirmed, "订单不存在")


async def cancel_order(session: AsyncSession, user_id: UUID, order_id: UUID) -> dict[str, object]:
    result = await session.execute(
        text(
            f"""
            UPDATE orders SET status = 'fail', failure_reason = 'user_cancelled'
            WHERE id = :id AND user_id = :user_id AND status = 'pending'
            RETURNING {ORDER_COLUMNS.replace("o.", "")}
            """
        ),
        {"id": order_id, "user_id": user_id},
    )
    value = result.mappings().first()
    if value is not None:
        return dict(value)
    current = await session.execute(
        text(f"SELECT {ORDER_COLUMNS} FROM orders o WHERE o.id = :id AND o.user_id = :user_id"),
        {"id": order_id, "user_id": user_id},
    )
    return row_or_404(current, "订单不存在")
