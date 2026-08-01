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
    required_slots: list[str],
    optional_slots: list[str],
) -> dict[str, Any]:
    """Validate one product against the platform-maintained slot definition."""
    allowed_slots = {*required_slots, *optional_slots}
    unexpected = set(attributes) - allowed_slots
    if unexpected:
        names = "、".join(sorted(unexpected))
        raise ValueError(f"{category_l2} 包含未定义的槽位：{names}")

    missing = [slot for slot in required_slots if _is_empty(attributes.get(slot))]
    if missing:
        raise ValueError(f"请填写必填槽位：{'、'.join(missing)}")

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
            SELECT category_l1, category_l2, required_slots, optional_slots
            FROM categories
            WHERE category_l2 = :category_l2
            """
        ),
        {"category_l2": category_l2},
    )
    category = result.mappings().first()
    if category is None:
        raise ValueError(f"二级品类不存在：{category_l2}")
    if category["category_l1"] != category_l1:
        raise ValueError("所选二级品类不属于该一级品类")
    return validate_attributes(
        category_l2,
        attributes,
        list(category["required_slots"]),
        list(category["optional_slots"]),
    )


async def list_categories(session: AsyncSession) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT id, category_l1, category_l2, required_slots, optional_slots,
                   created_at, updated_at
            FROM categories
            ORDER BY category_l1, category_l2, created_at
            """
        )
    )
    return [dict(row) for row in result.mappings().all()]
