from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

REQUIRED_ATTRIBUTE_KEYS_BY_CATEGORY: dict[str, tuple[str, ...]] = {
    "HEADPHONES": (
        "noiseCancellation",
        "form",
        "connectivity",
        "batteryHours",
    ),
    "COFFEE_MACHINE": (
        "type",
        "steamWand",
        "pressureBar",
        "waterTankMl",
    ),
    "ELECTRIC_KETTLE": (
        "capacityL",
        "temperatureControl",
        "keepWarm",
    ),
    "RUNNING_SHOES": (
        "gender",
        "size",
        "terrain",
        "cushion",
        "footType",
    ),
    "WATCHES": (
        "gender",
        "movement",
        "material",
        "waterResistance",
    ),
    "LIPSTICK": (
        "shade",
        "finish",
        "skinType",
    ),
}

ATTRIBUTE_KEYS_BY_CATEGORY: dict[str, frozenset[str]] = {
    category: frozenset(keys) for category, keys in REQUIRED_ATTRIBUTE_KEYS_BY_CATEGORY.items()
}


def normalize_attributes(category_l2: str, attributes: dict[str, Any]) -> dict[str, Any]:
    expected = ATTRIBUTE_KEYS_BY_CATEGORY.get(category_l2)
    if expected is None:
        raise ValueError(f"不支持的二级品类：{category_l2}")
    unexpected = set(attributes) - expected
    if unexpected:
        names = "、".join(sorted(unexpected))
        raise ValueError(f"{category_l2} 包含未定义的 attributes：{names}")
    return {key: attributes.get(key) for key in sorted(expected)}


def validate_attributes(
    category_l2: str,
    attributes: dict[str, Any],
    slots: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate one product against the platform-maintained slot definition."""
    allowed_slots = {str(slot["key"]) for slot in slots}
    unexpected = set(attributes) - allowed_slots
    if unexpected:
        names = "、".join(sorted(unexpected))
        raise ValueError(f"{category_l2} 包含未定义的槽位：{names}")

    missing = [
        str(slot["key"])
        for slot in slots
        if slot["is_required"] and _is_empty(attributes.get(str(slot["key"])))
    ]
    if missing:
        raise ValueError(f"请填写必填槽位：{'、'.join(missing)}")

    invalid = []
    for slot in slots:
        key = str(slot["key"])
        value = attributes.get(key)
        if _is_empty(value):
            continue
        enum_values = slot["enum_values"]
        if isinstance(value, list):
            is_valid = bool(value) and all(item in enum_values for item in value)
        else:
            is_valid = value in enum_values
        if not is_valid:
            invalid.append(key)
    if invalid:
        raise ValueError(f"槽位值不在平台枚举范围内：{'、'.join(invalid)}")

    return {key: value for key, value in attributes.items() if not _is_empty(value)}


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


async def validate_product_taxonomy(
    session: AsyncSession,
    category_l1: str,
    category_l2: str,
    attributes: dict[str, Any],
) -> dict[str, Any]:
    result = await session.execute(
        text(
            """
            SELECT c.id, g.code AS category_l1, c.category_l2
            FROM category_l2 c
            JOIN category_l1 g ON g.id = c.category_l1_id
            WHERE c.category_l2 = :category_l2
            """
        ),
        {"category_l2": category_l2},
    )
    category = result.mappings().first()
    if category is None:
        raise ValueError(f"二级品类不存在：{category_l2}")
    if category["category_l1"] != category_l1:
        raise ValueError("所选二级品类不属于该一级品类")
    slot_result = await session.execute(
        text(
            """
            SELECT key, is_required, enum_values
            FROM category_slots
            WHERE category_id = :category_id
            ORDER BY created_at, key
            """
        ),
        {"category_id": category["id"]},
    )
    slots = [dict(row) for row in slot_result.mappings()]
    return validate_attributes(category_l2, attributes, slots)


async def list_categories(session: AsyncSession) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT c.id, c.category_l1_id, g.code AS category_l1, c.category_l2,
                   COALESCE(
                       array_agg(s.key ORDER BY s.created_at, s.key)
                           FILTER (WHERE s.is_required), ARRAY[]::text[]
                   ) AS required_slots,
                   COALESCE(
                       array_agg(s.key ORDER BY s.created_at, s.key)
                           FILTER (WHERE NOT s.is_required), ARRAY[]::text[]
                   ) AS optional_slots,
                   COALESCE(
                       jsonb_agg(
                           jsonb_build_object(
                               'id', s.id,
                               'key', s.key,
                               'is_required', s.is_required,
                               'enum_values', s.enum_values
                           ) ORDER BY s.created_at, s.key
                       ) FILTER (WHERE s.id IS NOT NULL), '[]'::jsonb
                   ) AS slots,
                   c.created_at, c.updated_at
            FROM category_l2 c
            JOIN category_l1 g ON g.id = c.category_l1_id
            LEFT JOIN category_slots s ON s.category_id = c.id
            GROUP BY c.id, c.category_l1_id, g.code, c.category_l2, c.created_at, c.updated_at
            ORDER BY g.code, c.category_l2, c.created_at
            """
        )
    )
    return [dict(row) for row in result.mappings().all()]
