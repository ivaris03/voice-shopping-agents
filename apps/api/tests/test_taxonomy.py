from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from voice_shopping_api.agents.workflow import REQUIRED_SLOTS
from voice_shopping_api.core.taxonomy import ATTRIBUTE_KEYS_BY_CATEGORY, normalize_attributes
from voice_shopping_api.schemas.domain import ProductCreate


def test_normalize_attributes_fills_every_category_key_with_null() -> None:
    attributes = normalize_attributes(
        "RUNNING_SHOES", {"terrain": "road", "cushion": "high"}
    )

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


def test_product_create_persists_the_complete_category_key_set() -> None:
    product = ProductCreate(
        merchant_id=UUID("10000000-0000-4000-8000-000000000004"),
        sku="TEST-RUN-001",
        name="测试跑鞋",
        category_l1="SPORTS",
        category_l2="RUNNING_SHOES",
        price=Decimal("599.00"),
        stock=10,
        attributes={"terrain": "road"},
    )

    assert set(product.attributes) == ATTRIBUTE_KEYS_BY_CATEGORY["RUNNING_SHOES"]
    assert product.attributes["terrain"] == "road"
    assert product.attributes["footType"] is None


def test_product_create_rejects_unknown_category() -> None:
    with pytest.raises(ValidationError, match="不支持的二级品类"):
        ProductCreate(
            merchant_id=UUID("10000000-0000-4000-8000-000000000004"),
            sku="TEST-UNKNOWN-001",
            name="未知商品",
            category_l1="UNKNOWN",
            category_l2="UNKNOWN",
            price=Decimal("1.00"),
            stock=1,
            attributes={},
        )
