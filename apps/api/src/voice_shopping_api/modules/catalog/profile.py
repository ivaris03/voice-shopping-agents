import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

RECENT_ITEMS_LIMIT = 20
CLICK_AFFINITY_WEIGHT = 0.1
ORDER_AFFINITY_WEIGHT = 0.32


def _bump(scores: dict[str, float], key: str | None, weight: float) -> dict[str, float]:
    updated = {name: float(value) for name, value in scores.items()}
    if key:
        updated[key] = round(min(1.0, updated.get(key, 0.0) + weight), 4)
    return updated


def _score_map(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    return {str(name): float(score) for name, score in value.items()}


def _append_recent(values: object, product_id: UUID) -> list[UUID]:
    """Append one product while keeping a deduplicated, bounded recent list."""
    if not isinstance(values, (list, tuple)):
        return [product_id]
    normalized: list[UUID] = []
    for value in values:
        try:
            parsed = value if isinstance(value, UUID) else UUID(str(value))
        except (TypeError, ValueError):
            continue
        if parsed != product_id and parsed not in normalized:
            normalized.append(parsed)
    normalized.append(product_id)
    return normalized[-RECENT_ITEMS_LIMIT:]


def _string_ids(values: object) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    return [str(value) for value in values]


async def update_profiles(
    session: AsyncSession,
    user_id: UUID,
    product_id: UUID,
    event_type: str,
) -> None:
    """Update the behavior-driven profile for a click or successful order.

    Static profile fields are intentionally not changed by product behavior.
    The dynamic row is locked before the read-modify-write operation so rapid
    events for one user do not overwrite each other's affinity increments.
    """
    if event_type not in {"click", "order"}:
        raise ValueError(f"Unsupported profile event: {event_type}")

    product_result = await session.execute(
        text(
            """
            SELECT category_l2, brand
            FROM products
            WHERE id = :product_id AND deleted_at IS NULL
            """
        ),
        {"product_id": product_id},
    )
    product = product_result.mappings().first()
    if product is None:
        raise HTTPException(status_code=404, detail="商品不存在")

    user_result = await session.execute(
        text("SELECT id FROM users WHERE id = :user_id FOR UPDATE"),
        {"user_id": user_id},
    )
    if user_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    dynamic_result = await session.execute(
        text(
            """
            SELECT category_affinity, brand_affinity, recent_viewed,
                   recent_purchased, price_sensitivity, avg_order_amount
            FROM user_profile_dynamic
            WHERE user_id = :user_id
            FOR UPDATE
            """
        ),
        {"user_id": user_id},
    )
    dynamic = dynamic_result.mappings().first()
    weight = ORDER_AFFINITY_WEIGHT if event_type == "order" else CLICK_AFFINITY_WEIGHT
    category_affinity = _bump(
        _score_map(dynamic["category_affinity"] if dynamic else None),
        product["category_l2"],
        weight,
    )
    brand_affinity = _bump(
        _score_map(dynamic["brand_affinity"] if dynamic else None),
        product["brand"],
        weight,
    )

    recent_viewed = list(dynamic["recent_viewed"] or []) if dynamic else []
    recent_purchased = list(dynamic["recent_purchased"] or []) if dynamic else []
    if event_type == "click":
        recent_viewed = _append_recent(recent_viewed, product_id)
    else:
        recent_purchased = _append_recent(recent_purchased, product_id)

    price_sensitivity = dynamic["price_sensitivity"] if dynamic else None
    avg_order_amount = dynamic["avg_order_amount"] if dynamic else None
    if event_type == "order":
        # The order is already marked success in the same transaction when
        # confirm_order calls this function, so this includes the new order.
        average_result = await session.execute(
            text(
                """
                SELECT AVG(total_amount)
                FROM orders
                WHERE user_id = :user_id AND status = 'success'
                """
            ),
            {"user_id": user_id},
        )
        avg_order_amount = average_result.scalar_one_or_none()

    now = datetime.now(UTC)
    await session.execute(
        text(
            """
            INSERT INTO user_profile_dynamic (
                user_id, category_affinity, brand_affinity, recent_viewed,
                recent_purchased, price_sensitivity, avg_order_amount, updated_at
            ) VALUES (
                :user_id, CAST(:category_affinity AS jsonb), CAST(:brand_affinity AS jsonb),
                :recent_viewed, :recent_purchased, :price_sensitivity, :avg_order_amount, :now
            )
            ON CONFLICT (user_id) DO UPDATE SET
                category_affinity = EXCLUDED.category_affinity,
                brand_affinity = EXCLUDED.brand_affinity,
                recent_viewed = EXCLUDED.recent_viewed,
                recent_purchased = EXCLUDED.recent_purchased,
                price_sensitivity = EXCLUDED.price_sensitivity,
                avg_order_amount = EXCLUDED.avg_order_amount,
                updated_at = EXCLUDED.updated_at
            """
        ),
        {
            "user_id": user_id,
            "category_affinity": json.dumps(category_affinity, ensure_ascii=False),
            "brand_affinity": json.dumps(brand_affinity, ensure_ascii=False),
            "recent_viewed": recent_viewed,
            "recent_purchased": recent_purchased,
            "price_sensitivity": price_sensitivity,
            "avg_order_amount": avg_order_amount,
            "now": now,
        },
    )


async def profile_snapshot(session: AsyncSession, user_id: UUID) -> dict[str, object]:
    result = await session.execute(
        text(
            """
            SELECT
                s.gender, s.age, s.city, s.height_cm, s.weight_kg,
                s.skin_type, s.tech_savvy, s.budget_band, s.locale,
                d.category_affinity, d.brand_affinity, d.recent_viewed,
                d.recent_purchased, d.price_sensitivity, d.avg_order_amount
            FROM users u
            LEFT JOIN user_profile_static s ON s.user_id = u.id
            LEFT JOIN user_profile_dynamic d ON d.user_id = u.id
            WHERE u.id = :user_id
            """
        ),
        {"user_id": user_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    return {
        "static": {
            "gender": row["gender"],
            "age": row["age"],
            "city": row["city"],
            "heightCm": row["height_cm"],
            "weightKg": row["weight_kg"],
            "skinType": row["skin_type"],
            "techSavvy": row["tech_savvy"],
            "budgetBand": row["budget_band"],
            "locale": row["locale"] or "zh_cn",
        },
        "dynamic": {
            "categoryAffinity": _score_map(row["category_affinity"]),
            "brandAffinity": _score_map(row["brand_affinity"]),
            "recentViewed": _string_ids(row["recent_viewed"]),
            "recentPurchased": _string_ids(row["recent_purchased"]),
            "priceSensitivity": (
                float(row["price_sensitivity"])
                if row["price_sensitivity"] is not None
                else None
            ),
            "avgOrderAmount": (
                float(row["avg_order_amount"])
                if row["avg_order_amount"] is not None
                else None
            ),
        },
        "capturedAt": datetime.now(UTC).isoformat(),
    }
