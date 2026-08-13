"""ProfileReranker pure-function tests."""

from voice_shopping_api.agents.profile_reranker import (
    RULE_BRAND_HIT,
    RULE_PRICE_OVER_AVG,
    RULE_REPEAT_PURCHASE,
    ProfileReranker,
)

PRODUCT = {
    "id": "20000000-0000-4000-8000-000000000101",
    "brand": "Asics",
    "price": 899,
    "vector_score": 0.7,
}


def _adjustments(profile: dict[str, object]) -> tuple[float, dict[str, float]]:
    return ProfileReranker._profile_adjustments(PRODUCT, profile)


def test_empty_profile_keeps_vector_score() -> None:
    ranked = ProfileReranker().rerank([PRODUCT], {})
    assert ranked[0].match_score == 0.7
    assert ranked[0].score_breakdown == {"vector": 0.7}


def test_brand_hit_adds_points() -> None:
    score, parts = _adjustments({"dynamic": {"brandAffinity": {"Asics": 0.18}}})
    assert score == RULE_BRAND_HIT
    assert parts == {"brandHit": RULE_BRAND_HIT}


def test_brand_with_zero_score_is_not_a_hit() -> None:
    score, parts = _adjustments({"dynamic": {"brandAffinity": {"Asics": 0.0}}})
    assert score == 0.0
    assert parts == {}


def test_budget_sensitivity_controls_acceptable_price_ceiling() -> None:
    sensitive = {"dynamic": {"avgOrderAmount": 500.0, "priceSensitivity": 0.8}}
    insensitive = {"dynamic": {"avgOrderAmount": 500.0, "priceSensitivity": 0.1}}
    assert _adjustments(sensitive) == (
        RULE_PRICE_OVER_AVG,
        {"priceOverAvgOrderAmount": RULE_PRICE_OVER_AVG},
    )
    assert _adjustments(insensitive) == (0.0, {})


def test_missing_price_sensitivity_uses_legacy_1_5x_ceiling() -> None:
    assert _adjustments({"dynamic": {"avgOrderAmount": 600.0}}) == (0.0, {})
    assert _adjustments({"dynamic": {"avgOrderAmount": 500.0}})[0] == RULE_PRICE_OVER_AVG


def test_repeat_purchase_deducts() -> None:
    profile = {"dynamic": {"recentPurchased": [PRODUCT["id"]]}}
    score, parts = _adjustments(profile)
    assert score == RULE_REPEAT_PURCHASE
    assert parts == {"repeatPurchase": RULE_REPEAT_PURCHASE}


def test_profile_reranks_all_twenty_candidates_before_top_three_cutoff() -> None:
    products = [
        {
            "id": f"product-{index}",
            "brand": "Other",
            "price": 500,
            "vector_score": 1.0 - index / 100,
        }
        for index in range(20)
    ]
    products[19]["brand"] = "Preferred"

    ranked = ProfileReranker().rerank(
        products,
        {"dynamic": {"brandAffinity": {"Preferred": 0.8}}},
    )

    assert len(ranked) == 3
    assert ranked[0].product["id"] == "product-19"
    assert ranked[0].score_breakdown["brandHit"] == RULE_BRAND_HIT


def test_cold_start_preserves_pgvector_order_on_ties() -> None:
    products = [
        {"id": "first", "vector_score": 0.8},
        {"id": "second", "vector_score": 0.8},
    ]
    ranked = ProfileReranker().rerank(products, {})
    assert [item.product["id"] for item in ranked] == ["first", "second"]


def test_match_score_is_clamped_to_public_range() -> None:
    boosted = dict(PRODUCT, vector_score=0.95)
    ranked = ProfileReranker().rerank([boosted], {"dynamic": {"brandAffinity": {"Asics": 1.0}}})
    assert ranked[0].match_score == 1.0
