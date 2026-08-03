import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

RECENT_ITEMS_LIMIT = 20
CLICK_AFFINITY_WEIGHT = 0.1
ORDER_AFFINITY_WEIGHT = 0.32

STATIC_PROFILE_FIELDS = (
    "gender",
    "age",
    "city",
    "height_cm",
    "weight_kg",
    "skin_type",
    "tech_savvy",
    "budget_band",
    "locale",
)

_STATIC_PROFILE_ALIASES = {
    "gender": "gender",
    "age": "age",
    "city": "city",
    "height": "height_cm",
    "heightCm": "height_cm",
    "height_cm": "height_cm",
    "weight": "weight_kg",
    "weightKg": "weight_kg",
    "weight_kg": "weight_kg",
    "skinType": "skin_type",
    "skin_type": "skin_type",
    "techSavvy": "tech_savvy",
    "tech_savvy": "tech_savvy",
    "budgetBand": "budget_band",
    "budget_band": "budget_band",
    "locale": "locale",
}

_CITY_NAMES = (
    "北京",
    "上海",
    "广州",
    "深圳",
    "杭州",
    "成都",
    "重庆",
    "武汉",
    "南京",
    "西安",
    "天津",
    "长沙",
    "郑州",
    "青岛",
    "厦门",
    "苏州",
    "宁波",
    "福州",
    "济南",
    "合肥",
    "昆明",
    "南昌",
    "贵阳",
    "太原",
    "石家庄",
    "沈阳",
    "大连",
    "哈尔滨",
    "长春",
    "南宁",
    "海口",
    "兰州",
    "乌鲁木齐",
    "东莞",
    "佛山",
    "无锡",
)


def _integer(value: object, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    return normalized if minimum <= normalized <= maximum else None


def _normalize_enum(value: object, aliases: Mapping[str, str], maximum_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = aliases.get(value.strip().lower(), value.strip().lower())
    return normalized[:maximum_length] if normalized and len(normalized) <= maximum_length else None


def _budget_band(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if amount < 0:
        return None
    if amount <= 500:
        return "budget"
    if amount <= 2000:
        return "mid"
    return "premium"


def normalize_static_profile_patch(value: Mapping[str, Any] | None) -> dict[str, object]:
    """Normalize profile facts from APIs, channels, or conversation extraction.

    ``None`` and invalid values are omitted so a low-confidence source cannot
    erase a previously known static profile value during the final merge.
    """
    if not isinstance(value, Mapping):
        return {}

    raw_values: dict[str, object] = {}
    for key, raw in value.items():
        field = _STATIC_PROFILE_ALIASES.get(str(key))
        if field and raw is not None:
            raw_values[field] = raw

    normalized: dict[str, object] = {}
    if (gender := _normalize_enum(raw_values.get("gender"), {
        "男": "male",
        "男性": "male",
        "man": "male",
        "女": "female",
        "女性": "female",
        "woman": "female",
        "中性": "unisex",
        "不限": "unisex",
    }, 8)) in {"male", "female", "unisex"}:
        normalized["gender"] = gender

    if (age := _integer(raw_values.get("age"), 0, 120)) is not None:
        normalized["age"] = age
    if isinstance(city := raw_values.get("city"), str):
        city = city.strip().removesuffix("市")
        if city and len(city) <= 32:
            normalized["city"] = city
    if (height_cm := _integer(raw_values.get("height_cm"), 50, 250)) is not None:
        normalized["height_cm"] = height_cm
    if (weight_kg := _integer(raw_values.get("weight_kg"), 10, 300)) is not None:
        normalized["weight_kg"] = weight_kg

    if (skin_type := _normalize_enum(raw_values.get("skin_type"), {
        "干皮": "dry",
        "干性": "dry",
        "油皮": "oily",
        "油性": "oily",
        "敏感": "sensitive",
        "敏感肌": "sensitive",
        "中性": "normal",
        "正常肤质": "normal",
    }, 16)) in {"dry", "oily", "sensitive", "normal"}:
        normalized["skin_type"] = skin_type

    if (tech_savvy := _normalize_enum(raw_values.get("tech_savvy"), {
        "新手": "novice",
        "小白": "novice",
        "入门": "novice",
        "中级": "mid",
        "熟悉": "mid",
        "专家": "expert",
        "专业": "expert",
    }, 16)) in {"novice", "mid", "expert"}:
        normalized["tech_savvy"] = tech_savvy

    if (budget_band := _normalize_enum(raw_values.get("budget_band"), {
        "经济": "budget",
        "入门": "budget",
        "中端": "mid",
        "中档": "mid",
        "高端": "premium",
        "奢侈": "premium",
    }, 16)) in {"budget", "mid", "premium"}:
        normalized["budget_band"] = budget_band
    elif (budget := _budget_band(value.get("budget"))) is not None:
        normalized["budget_band"] = budget

    if isinstance(locale := raw_values.get("locale"), str):
        locale = locale.strip()
        if locale and len(locale) <= 16:
            normalized["locale"] = locale
    return normalized


def merge_static_profile_patches(
    *patches: Mapping[str, Any] | None,
) -> dict[str, object]:
    """Merge profile facts from oldest to newest, ignoring invalid values."""
    merged: dict[str, object] = {}
    for patch in patches:
        merged.update(normalize_static_profile_patch(patch))
    return merged


def extract_static_profile_candidates(
    utterance: str,
    slots: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    """Extract high-confidence static facts from the current text/audio turn."""
    text_value = utterance.strip()
    candidates: dict[str, object] = {}
    if age_match := re.search(r"(?:今年|我今年|本人今年)?\s*(\d{1,3})\s*岁", text_value):
        candidates["age"] = int(age_match.group(1))
    if height_match := re.search(r"(?:身高|高)\s*(\d{2,3})\s*(?:cm|厘米)?", text_value, re.I):
        candidates["height_cm"] = int(height_match.group(1))
    if weight_match := re.search(
        r"(?:体重|体重是|重)\s*(\d{2,3})\s*(?:kg|公斤)?", text_value, re.I
    ):
        candidates["weight_kg"] = int(weight_match.group(1))

    for city in _CITY_NAMES:
        if re.search(
            rf"(?:住在|生活在|来自|常住|家在|所在城市(?:是)?|城市是)\s*{re.escape(city)}(?:市)?",
            text_value,
        ):
            candidates["city"] = city
            break

    if re.search(r"(?:我是|本人是|性别是)\s*(?:男|男性)", text_value):
        candidates["gender"] = "male"
    elif re.search(r"(?:我是|本人是|性别是)\s*(?:女|女性)", text_value):
        candidates["gender"] = "female"
    if any(word in text_value for word in ("不太懂电脑", "电脑小白", "科技小白", "新手")):
        candidates["tech_savvy"] = "novice"
    elif any(word in text_value for word in ("很懂数码", "熟悉数码", "中级用户")):
        candidates["tech_savvy"] = "mid"
    elif any(word in text_value for word in ("数码专家", "专业玩家", "发烧友")):
        candidates["tech_savvy"] = "expert"

    slot_values = slots if isinstance(slots, Mapping) else {}
    if slot_values.get("skinType") is not None:
        candidates["skin_type"] = slot_values["skinType"]
    if (budget_max := slot_values.get("budgetMax")) is not None:
        candidates["budget"] = budget_max
    return normalize_static_profile_patch(candidates)


async def update_static_profile(
    session: AsyncSession,
    user_id: UUID,
    patch: Mapping[str, Any] | None,
) -> list[str]:
    """Merge a validated patch into the canonical static profile row."""
    normalized = normalize_static_profile_patch(patch)
    if not normalized:
        return []

    user_result = await session.execute(
        text("SELECT id FROM users WHERE id = :user_id FOR UPDATE"),
        {"user_id": user_id},
    )
    if user_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    await session.execute(
        text(
            """
            INSERT INTO user_profile_static (
                user_id, gender, age, city, height_cm, weight_kg,
                skin_type, tech_savvy, budget_band, locale
            ) VALUES (
                :user_id, :gender, :age, :city, :height_cm, :weight_kg,
                :skin_type, :tech_savvy, :budget_band, COALESCE(:locale, 'zh_cn')
            )
            ON CONFLICT (user_id) DO UPDATE SET
                gender = COALESCE(EXCLUDED.gender, user_profile_static.gender),
                age = COALESCE(EXCLUDED.age, user_profile_static.age),
                city = COALESCE(EXCLUDED.city, user_profile_static.city),
                height_cm = COALESCE(EXCLUDED.height_cm, user_profile_static.height_cm),
                weight_kg = COALESCE(EXCLUDED.weight_kg, user_profile_static.weight_kg),
                skin_type = COALESCE(EXCLUDED.skin_type, user_profile_static.skin_type),
                tech_savvy = COALESCE(EXCLUDED.tech_savvy, user_profile_static.tech_savvy),
                budget_band = COALESCE(EXCLUDED.budget_band, user_profile_static.budget_band),
                locale = COALESCE(EXCLUDED.locale, user_profile_static.locale)
            """
        ),
        {
            "user_id": user_id,
            "gender": normalized.get("gender"),
            "age": normalized.get("age"),
            "city": normalized.get("city"),
            "height_cm": normalized.get("height_cm"),
            "weight_kg": normalized.get("weight_kg"),
            "skin_type": normalized.get("skin_type"),
            "tech_savvy": normalized.get("tech_savvy"),
            "budget_band": normalized.get("budget_band"),
            "locale": normalized.get("locale"),
        },
    )
    return list(normalized)


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
