from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _bump(scores: dict[str, float], key: str | None, weight: float) -> dict[str, float]:
    updated = {name: float(value) for name, value in scores.items()}
    if key:
        updated[key] = round(min(1.0, updated.get(key, 0.0) + weight), 4)
    return updated


async def update_profiles(
    session: AsyncSession,
    user_id: UUID,
    product_id: UUID,
    event_type: str,
) -> None:
    product_result = await session.execute(
        text(
            """
            SELECT category_l2, brand, price
            FROM products
            WHERE id = :product_id AND deleted_at IS NULL
            """
        ),
        {"product_id": product_id},
    )
    product = product_result.mappings().first()
    if product is None:
        raise HTTPException(status_code=404, detail="商品不存在")

    static_weight = 0.18 if event_type == "order" else 0.04
    dynamic_weight = 0.32 if event_type == "order" else 0.1
    now = datetime.now(UTC)

    static_result = await session.execute(
        text(
            """
            SELECT category_scores, brand_scores, attribute_preferences, price_min, price_max
            FROM user_static_profiles WHERE user_id = :user_id
            """
        ),
        {"user_id": user_id},
    )
    static = static_result.mappings().first()
    category_scores = _bump(
        dict(static["category_scores"]) if static else {}, product["category_l2"], static_weight
    )
    brand_scores = _bump(
        dict(static["brand_scores"]) if static else {}, product["brand"], static_weight
    )
    current_min = static["price_min"] if static else None
    current_max = static["price_max"] if static else None
    price = Decimal(product["price"])
    price_min = min(current_min, price) if current_min is not None else price
    price_max = max(current_max, price) if current_max is not None else price
    await session.execute(
        text(
            """
            INSERT INTO user_static_profiles (
                user_id, category_scores, brand_scores, attribute_preferences,
                price_min, price_max, last_event_at
            ) VALUES (
                :user_id, CAST(:category_scores AS jsonb), CAST(:brand_scores AS jsonb),
                '{}'::jsonb, :price_min, :price_max, :now
            )
            ON CONFLICT (user_id) DO UPDATE SET
                category_scores = EXCLUDED.category_scores,
                brand_scores = EXCLUDED.brand_scores,
                price_min = EXCLUDED.price_min,
                price_max = EXCLUDED.price_max,
                version = user_static_profiles.version + 1,
                last_event_at = EXCLUDED.last_event_at
            """
        ),
        {
            "user_id": user_id,
            "category_scores": __import__("json").dumps(category_scores, ensure_ascii=False),
            "brand_scores": __import__("json").dumps(brand_scores, ensure_ascii=False),
            "price_min": price_min,
            "price_max": price_max,
            "now": now,
        },
    )

    dynamic_result = await session.execute(
        text(
            """
            SELECT category_scores, product_scores, session_interests
            FROM user_dynamic_profiles WHERE user_id = :user_id
            """
        ),
        {"user_id": user_id},
    )
    dynamic = dynamic_result.mappings().first()
    dynamic_categories = _bump(
        dict(dynamic["category_scores"]) if dynamic else {},
        product["category_l2"],
        dynamic_weight,
    )
    product_scores = _bump(
        dict(dynamic["product_scores"]) if dynamic else {}, str(product_id), dynamic_weight
    )
    interests = dict(dynamic["session_interests"]) if dynamic else {}
    interests.update({"lastCategory": product["category_l2"], "lastEvent": event_type})
    await session.execute(
        text(
            """
            INSERT INTO user_dynamic_profiles (
                user_id, category_scores, product_scores, session_interests,
                last_event_at, expires_at
            ) VALUES (
                :user_id, CAST(:category_scores AS jsonb), CAST(:product_scores AS jsonb),
                CAST(:interests AS jsonb), :now, :expires_at
            )
            ON CONFLICT (user_id) DO UPDATE SET
                category_scores = EXCLUDED.category_scores,
                product_scores = EXCLUDED.product_scores,
                session_interests = EXCLUDED.session_interests,
                version = user_dynamic_profiles.version + 1,
                last_event_at = EXCLUDED.last_event_at,
                expires_at = EXCLUDED.expires_at
            """
        ),
        {
            "user_id": user_id,
            "category_scores": __import__("json").dumps(dynamic_categories, ensure_ascii=False),
            "product_scores": __import__("json").dumps(product_scores, ensure_ascii=False),
            "interests": __import__("json").dumps(interests, ensure_ascii=False),
            "now": now,
            "expires_at": now + timedelta(days=7),
        },
    )


async def profile_snapshot(session: AsyncSession, user_id: UUID) -> dict[str, object]:
    result = await session.execute(
        text(
            """
            SELECT
                s.category_scores AS static_categories,
                s.brand_scores, s.attribute_preferences, s.price_min, s.price_max,
                d.category_scores AS dynamic_categories,
                d.product_scores, d.session_interests, d.expires_at,
                (SELECT avg(o.total_amount) FROM orders o
                 WHERE o.user_id = u.id AND o.status = 'success') AS avg_order_value,
                COALESCE((
                    SELECT array_agg(DISTINCT o.product_id)
                    FROM orders o
                    WHERE o.user_id = u.id AND o.status = 'success'
                      AND o.created_at >= now() - interval '90 days'
                ), '{}'::uuid[]) AS recently_purchased_ids
            FROM users u
            LEFT JOIN user_static_profiles s ON s.user_id = u.id
            LEFT JOIN user_dynamic_profiles d ON d.user_id = u.id
            WHERE u.id = :user_id
            """
        ),
        {"user_id": user_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    dynamic_valid = row["expires_at"] is None or row["expires_at"] > datetime.now(UTC)
    return {
        "static": {
            "categoryScores": dict(row["static_categories"] or {}),
            "brandScores": dict(row["brand_scores"] or {}),
            "attributePreferences": dict(row["attribute_preferences"] or {}),
            "priceRange": {
                "min": float(row["price_min"]) if row["price_min"] is not None else None,
                "max": float(row["price_max"]) if row["price_max"] is not None else None,
            },
        },
        "dynamic": {
            "categoryScores": dict(row["dynamic_categories"] or {}) if dynamic_valid else {},
            "productScores": dict(row["product_scores"] or {}) if dynamic_valid else {},
            "sessionInterests": dict(row["session_interests"] or {}) if dynamic_valid else {},
        },
        # 推荐二次排序的规则输入：平均客单价（success 订单均价）与最近买过的商品。
        "avgOrderValue": (
            float(row["avg_order_value"]) if row["avg_order_value"] is not None else None
        ),
        "recentlyPurchasedProductIds": [
            str(product_id) for product_id in (row["recently_purchased_ids"] or [])
        ],
        "capturedAt": datetime.now(UTC).isoformat(),
    }
