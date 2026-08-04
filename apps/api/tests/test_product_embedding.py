from decimal import Decimal

import pytest

from voice_shopping_api.core.embeddings import normalize_embedding
from voice_shopping_api.core.product_embedding import (
    ATTRIBUTE_KEY_LABELS,
    CATEGORY_L2_LABELS,
    build_product_embedding_text,
    embedding_text_for_product,
    price_band_label,
)
from voice_shopping_api.core.taxonomy import ATTRIBUTE_KEYS_BY_CATEGORY


def test_full_card_covers_all_fields() -> None:
    text_ = build_product_embedding_text(
        name="Sony WH-CH720N 无线降噪头戴耳机",
        category_l1="ELECTRONICS",
        category_l2="HEADPHONES",
        brand="Sony",
        description="轻量头戴式主动降噪耳机，适合通勤。",
        attributes={
            "form": "over-ear",
            "noiseCancellation": True,
            "batteryHours": 45,
            "color": "雾灰",
        },
        selling_points=["主动降噪适合通勤", "约 45 小时续航"],
        price=Decimal("699.00"),
    )

    expected_prefix = "商品：Sony WH-CH720N 无线降噪头戴耳机；品类：数码电子-耳机；品牌：Sony"
    assert text_.startswith(expected_prefix)
    assert "卖点：主动降噪适合通勤、约 45 小时续航" in text_
    assert "描述：轻量头戴式主动降噪耳机，适合通勤。" in text_
    assert "属性：续航时长：45小时、颜色：雾灰、形态：头戴式、主动降噪：是" in text_
    assert "价格：500-1000元" in text_


def test_unknown_codes_fall_back_to_raw_values() -> None:
    text_ = build_product_embedding_text(
        name="测试商品",
        category_l1="PETS",
        category_l2="CAT_FOOD",
        attributes={"flavor": "chicken", "unknownKey": "x"},
    )

    assert "品类：PETS-CAT_FOOD" in text_
    assert "flavor：chicken" in text_
    assert "unknownKey：x" in text_


def test_attribute_value_rendering() -> None:
    text_ = build_product_embedding_text(
        name="测试跑鞋",
        category_l1="SPORTS",
        category_l2="RUNNING_SHOES",
        attributes={
            "gender": "female",
            "sizeRange": [36, 46],
            "terrain": "road",
            "footType": ["flat", "overpronation"],
            "waterResistance": "100m",
            "pressureBar": 19,
            "capacityL": 1.5,
        },
    )

    assert "适用性别：女款" in text_
    assert "尺码范围：36-46码" in text_
    assert "适用路面：公路" in text_
    assert "足型：扁平足、过度内旋" in text_
    assert "防水深度：100米" in text_
    assert "萃取压力：19巴" in text_
    assert "容量：1.5升" in text_


def test_empty_sections_are_skipped() -> None:
    text_ = build_product_embedding_text(
        name="极简商品", category_l1="FASHION", category_l2="WATCHES"
    )

    assert text_ == "商品：极简商品；品类：时尚配饰-腕表"


def test_null_and_empty_attribute_values_are_skipped() -> None:
    text_ = build_product_embedding_text(
        name="商品",
        category_l1="BEAUTY",
        category_l2="LIPSTICK",
        attributes={"shade": "milk-tea", "skinType": None, "finish": ""},
    )

    assert "色号：奶茶色" in text_
    assert "肤质" not in text_
    assert "妆效" not in text_


def test_price_band_boundaries() -> None:
    assert price_band_label(Decimal("299")) == "300元以内"
    assert price_band_label(Decimal("300")) == "300-500元"
    assert price_band_label(Decimal("999")) == "500-1000元"
    assert price_band_label(Decimal("1000")) == "1000-2000元"
    assert price_band_label(Decimal("5000")) == "5000元以上"
    assert price_band_label(Decimal("5001")) == "5000元以上"


def test_price_band_not_included_when_price_missing() -> None:
    text_ = build_product_embedding_text(
        name="商品", category_l1="SPORTS", category_l2="RUNNING_SHOES"
    )

    assert "价格" not in text_


def test_normalize_embedding_returns_unit_vector() -> None:
    vector = normalize_embedding([3.0, 4.0])

    assert vector[0] == pytest.approx(0.6)
    assert vector[1] == pytest.approx(0.8)


def test_embedding_text_for_product_accepts_db_row_shape() -> None:
    card = embedding_text_for_product(
        {
            "name": "Sony WH-CH720N 无线降噪头戴耳机",
            "category_l1": "ELECTRONICS",
            "category_l2": "HEADPHONES",
            "brand": "Sony",
            "description": "轻量头戴式主动降噪耳机，适合通勤。",
            "attributes": {"form": "over-ear"},
            "selling_points": ["主动降噪适合通勤"],
            "price": Decimal("699.00"),
        }
    )

    assert card.startswith("商品：Sony WH-CH720N 无线降噪头戴耳机；品类：数码电子-耳机；品牌：Sony")
    assert "形态：头戴式" in card


def test_every_taxonomy_category_and_slot_has_a_chinese_label() -> None:
    for category, keys in ATTRIBUTE_KEYS_BY_CATEGORY.items():
        assert category in CATEGORY_L2_LABELS
        for key in keys:
            assert key in ATTRIBUTE_KEY_LABELS
