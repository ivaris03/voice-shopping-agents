"""Deterministic profile-aware reranking for vector-recalled products."""

from dataclasses import dataclass
from typing import Any

RULE_BRAND_HIT = 0.2
RULE_PRICE_OVER_AVG = -0.15
RULE_REPEAT_PURCHASE = -0.3
DEFAULT_PRICE_SENSITIVITY = 0.5
MAX_CANDIDATES = 20
TOP_PRODUCTS = 3


def _clamp(value: object, minimum: float = 0.0, maximum: float = 1.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return minimum
    return min(maximum, max(minimum, parsed))


@dataclass(frozen=True)
class RankedProduct:
    product: dict[str, Any]
    vector_score: float
    profile_score: float
    score_breakdown: dict[str, float]

    @property
    def match_score(self) -> float:
        return round(_clamp(self.vector_score + self.profile_score), 4)


class ProfileReranker:
    """Apply brand, purchase-history, and budget signals to vector candidates.

    The input order is already PGVector similarity order. Python's stable sort
    therefore preserves that order whenever two candidates receive the same
    final score, including the cold-start profile case.
    """

    def __init__(self, *, max_candidates: int = MAX_CANDIDATES) -> None:
        self.max_candidates = max_candidates

    @staticmethod
    def _profile_adjustments(
        product: dict[str, Any], profile: dict[str, Any]
    ) -> tuple[float, dict[str, float]]:
        dynamic = profile.get("dynamic")
        if not isinstance(dynamic, dict):
            return 0.0, {}

        parts: dict[str, float] = {}
        brand = str(product.get("brand") or "")
        brand_affinity = dynamic.get("brandAffinity")
        if isinstance(brand_affinity, dict) and _clamp(brand_affinity.get(brand)) > 0:
            parts["brandHit"] = RULE_BRAND_HIT

        recent_purchased = dynamic.get("recentPurchased")
        if isinstance(recent_purchased, list) and str(product.get("id", "")) in {
            str(product_id) for product_id in recent_purchased
        }:
            parts["repeatPurchase"] = RULE_REPEAT_PURCHASE

        avg_order_amount = dynamic.get("avgOrderAmount")
        try:
            average = float(avg_order_amount)
            price = float(product.get("price", 0))
        except (TypeError, ValueError):
            average = 0.0
            price = 0.0
        if average > 0:
            sensitivity_value = dynamic.get("priceSensitivity")
            sensitivity = (
                DEFAULT_PRICE_SENSITIVITY
                if sensitivity_value is None
                else _clamp(sensitivity_value)
            )
            # Sensitive users are penalized just above their normal spend;
            # insensitive users get up to a 2x average-order allowance.
            price_ceiling = average * (2.0 - sensitivity)
            if price > price_ceiling:
                parts["priceOverAvgOrderAmount"] = RULE_PRICE_OVER_AVG

        return sum(parts.values()), parts

    def rerank(
        self,
        products: list[dict[str, Any]],
        profile: dict[str, Any],
        *,
        limit: int = TOP_PRODUCTS,
    ) -> list[RankedProduct]:
        ranked = []
        for product in products[: self.max_candidates]:
            vector_score = _clamp(product.get("vector_score"))
            profile_score, profile_parts = self._profile_adjustments(product, profile)
            ranked.append(
                RankedProduct(
                    product=product,
                    vector_score=vector_score,
                    profile_score=profile_score,
                    score_breakdown={"vector": round(vector_score, 4), **profile_parts},
                )
            )
        return sorted(
            ranked,
            key=lambda item: item.vector_score + item.profile_score,
            reverse=True,
        )[:limit]
