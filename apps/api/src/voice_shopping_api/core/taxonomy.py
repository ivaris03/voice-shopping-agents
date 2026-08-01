from typing import Any

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
    category: frozenset(keys)
    for category, keys in REQUIRED_ATTRIBUTE_KEYS_BY_CATEGORY.items()
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
