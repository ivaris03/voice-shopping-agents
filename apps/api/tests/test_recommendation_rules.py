"""画像快照规则二次排序的纯函数测试。"""

from voice_shopping_api.agents.nodes.recommendation import (
    RULE_BRAND_HIT,
    RULE_PRICE_OVER_AVG,
    RULE_REPEAT_PURCHASE,
    _match_score,
    _reranker_score,
    _rule_adjustments,
)

PRODUCT = {
    "id": "20000000-0000-4000-8000-000000000101",
    "brand": "Asics",
    "price": 899,
    "name": "日常缓震跑鞋",
    "description": "",
    "selling_points": [],
    "attributes": {},
}


def test_empty_profile_adds_no_adjustment() -> None:
    score, parts = _rule_adjustments(PRODUCT, {})
    assert score == 0.0
    assert parts == {}


def test_brand_hit_adds_points() -> None:
    profile = {"dynamic": {"brandAffinity": {"Asics": 0.18}}}
    score, parts = _rule_adjustments(PRODUCT, profile)
    assert score == RULE_BRAND_HIT
    assert parts == {"brandHit": RULE_BRAND_HIT}


def test_brand_with_zero_score_is_not_a_hit() -> None:
    profile = {"dynamic": {"brandAffinity": {"Asics": 0.0}}}
    score, parts = _rule_adjustments(PRODUCT, profile)
    assert score == 0.0
    assert parts == {}


def test_price_over_1_5x_avg_order_value_deducts() -> None:
    profile = {"dynamic": {"avgOrderAmount": 500.0}}  # 899 > 750
    score, parts = _rule_adjustments(PRODUCT, profile)
    assert score == RULE_PRICE_OVER_AVG
    assert parts == {"priceOverAvgOrderAmount": RULE_PRICE_OVER_AVG}


def test_price_below_1_5x_avg_order_value_is_not_deducted() -> None:
    profile = {"dynamic": {"avgOrderAmount": 600.0}}  # 899 < 900
    score, parts = _rule_adjustments(PRODUCT, profile)
    assert score == 0.0
    assert parts == {}


def test_price_over_avg_without_orders_is_not_deducted() -> None:
    profile = {"dynamic": {"avgOrderAmount": None}}
    score, parts = _rule_adjustments(PRODUCT, profile)
    assert score == 0.0
    assert parts == {}


def test_repeat_purchase_deducts() -> None:
    profile = {"dynamic": {"recentPurchased": [PRODUCT["id"]]}}
    score, parts = _rule_adjustments(PRODUCT, profile)
    assert score == RULE_REPEAT_PURCHASE
    assert parts == {"repeatPurchase": RULE_REPEAT_PURCHASE}


def test_brand_hit_and_repeat_purchase_combine() -> None:
    profile = {
        "dynamic": {
            "brandAffinity": {"Asics": 0.18},
            "recentPurchased": [PRODUCT["id"]],
        },
    }
    score, parts = _rule_adjustments(PRODUCT, profile)
    assert score == RULE_BRAND_HIT + RULE_REPEAT_PURCHASE  # 0.2 - 0.3 = -0.1
    assert parts == {"brandHit": RULE_BRAND_HIT, "repeatPurchase": RULE_REPEAT_PURCHASE}


def test_reranker_score_uses_model_score_when_present() -> None:
    product_id = PRODUCT["id"]
    score = _reranker_score(PRODUCT, {"utterance": "跑鞋"}, {product_id: 0.9})
    assert score == 0.9


def test_reranker_score_clamps_out_of_range() -> None:
    product_id = PRODUCT["id"]
    score = _reranker_score(PRODUCT, {"utterance": "跑鞋"}, {product_id: 1.7})
    assert score == 1.0
    score = _reranker_score(PRODUCT, {"utterance": "跑鞋"}, {product_id: -0.2})
    assert score == 0.0


def test_match_score_caps_brand_boost_at_one() -> None:
    assert _match_score(RULE_BRAND_HIT, 1.0) == 1.0


def test_reranker_score_lexical_fallback_without_model() -> None:
    # 无 LLM 分：名称命中 utterance 关键词 → 0.62；完全不命中 → 0.52
    hit = _reranker_score(PRODUCT, {"utterance": "跑鞋"}, {})
    miss = _reranker_score(PRODUCT, {"utterance": "毫无关系的内容"}, {})
    assert hit == 0.62
    assert miss == 0.52
