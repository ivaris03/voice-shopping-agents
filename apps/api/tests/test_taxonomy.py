from decimal import Decimal
from uuid import UUID

import pytest

from voice_shopping_api.agents.workflow import REQUIRED_SLOTS
from voice_shopping_api.core.taxonomy import (
    ATTRIBUTE_KEYS_BY_CATEGORY,
    normalize_attributes,
    validate_attributes,
)
from voice_shopping_api.schemas.domain import CategoryCreate, CategorySlotCreate, ProductCreate


def _slot(key: str, required: bool, values: list[object]) -> dict[str, object]:
    return {"key": key, "is_required": required, "enum_values": values}


def test_normalize_attributes_fills_every_category_key_with_null() -> None:
    attributes = normalize_attributes("RUNNING_SHOES", {"terrain": "road", "cushion": "high"})

    assert set(attributes) == ATTRIBUTE_KEYS_BY_CATEGORY["RUNNING_SHOES"]
    assert attributes["terrain"] == "road"
    assert attributes["cushion"] == "high"
    assert attributes["footType"] is None


def test_every_category_has_three_to_five_keys_and_uses_them_as_required_slots() -> None:
    for category, keys in ATTRIBUTE_KEYS_BY_CATEGORY.items():
        assert 3 <= len(keys) <= 5
        assert set(REQUIRED_SLOTS[category]) == keys


def test_normalize_attributes_rejects_undefined_keys() -> None:
    with pytest.raises(ValueError, match="未定义"):
        normalize_attributes("RUNNING_SHOES", {"unknownKey": True})


def test_product_create_accepts_database_driven_category_without_static_normalization() -> None:
    product = ProductCreate(
        merchant_id=UUID("10000000-0000-4000-8000-000000000004"),
        sku="TEST-RUN-001",
        name="测试跑鞋",
        category_l1="PETS",
        category_l2="CAT_FOOD",
        price=Decimal("599.00"),
        stock=10,
        attributes={"flavor": "chicken"},
    )

    assert product.category_l2 == "CAT_FOOD"
    assert product.attributes == {"flavor": "chicken"}


def test_validate_attributes_requires_required_slots_and_allows_optional_slots() -> None:
    with pytest.raises(ValueError, match="flavor"):
        validate_attributes(
            "CAT_FOOD",
            {"weightKg": 2},
            [_slot("flavor", True, ["鸡肉", "鱼肉"]), _slot("weightKg", False, [1, 2])],
        )

    assert validate_attributes(
        "CAT_FOOD",
        {"flavor": "鸡肉", "weightKg": None},
        [_slot("flavor", True, ["鸡肉", "鱼肉"]), _slot("weightKg", False, [1, 2])],
    ) == {"flavor": "鸡肉"}


def test_validate_attributes_rejects_undefined_slots() -> None:
    with pytest.raises(ValueError, match="未定义"):
        validate_attributes(
            "CAT_FOOD", {"weightKg": 2, "unknown": True}, [_slot("weightKg", True, [1, 2])]
        )


def test_required_false_and_zero_are_valid_values() -> None:
    assert validate_attributes(
        "DRINK",
        {"sugarFree": False, "minimumAge": 0},
        [_slot("sugarFree", True, [True, False]), _slot("minimumAge", True, [0, 18])],
    ) == {
        "sugarFree": False,
        "minimumAge": 0,
    }


def test_secondary_category_requires_parent_id() -> None:
    category = CategoryCreate(
        category_l1_id=UUID("10000000-0000-4000-8000-000000000001"),
        category_l2="DRINK",
    )

    assert category.category_l2 == "DRINK"


def test_slot_requires_non_empty_enum_values() -> None:
    with pytest.raises(ValueError, match="at least 1 item"):
        CategorySlotCreate(key="flavor", is_required=True, enum_values=[])

    slot = CategorySlotCreate(
        key="flavor", is_required=False, enum_values=[" chicken ", "chicken", "fish"]
    )
    assert slot.enum_values == ["chicken", "fish"]


def test_validate_attributes_rejects_value_outside_enum() -> None:
    with pytest.raises(ValueError, match="枚举范围"):
        validate_attributes(
            "CAT_FOOD", {"flavor": "beef"}, [_slot("flavor", True, ["chicken", "fish"])]
        )
