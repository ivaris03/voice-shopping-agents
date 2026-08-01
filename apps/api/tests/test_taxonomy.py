from decimal import Decimal
from uuid import UUID

import pytest

from voice_shopping_api.agents.workflow import REQUIRED_SLOTS
from voice_shopping_api.core.taxonomy import (
    ATTRIBUTE_KEYS_BY_CATEGORY,
    normalize_attributes,
    validate_attributes,
)
from voice_shopping_api.schemas.domain import CategoryCreate, ProductCreate


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
        validate_attributes("CAT_FOOD", {"weightKg": 2}, ["flavor"], ["weightKg"])

    assert validate_attributes(
        "CAT_FOOD", {"flavor": "鸡肉", "weightKg": None}, ["flavor"], ["weightKg"]
    ) == {"flavor": "鸡肉"}


def test_validate_attributes_rejects_undefined_slots() -> None:
    with pytest.raises(ValueError, match="未定义"):
        validate_attributes("CAT_FOOD", {"weightKg": 2, "unknown": True}, ["weightKg"], [])


def test_required_false_and_zero_are_valid_values() -> None:
    assert validate_attributes(
        "DRINK", {"sugarFree": False, "minimumAge": 0}, ["sugarFree", "minimumAge"], []
    ) == {
        "sugarFree": False,
        "minimumAge": 0,
    }


def test_category_rejects_slot_in_both_columns() -> None:
    with pytest.raises(ValueError, match="同时为必填和选填"):
        CategoryCreate(
            category_l1="FOOD",
            category_l2="DRINK",
            required_slots=["flavor"],
            optional_slots=["flavor"],
        )
