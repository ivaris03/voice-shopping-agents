import logging
import re
from decimal import Decimal
from typing import Any

from langgraph.runtime import Runtime

from voice_shopping_api.agents.model import rerank_products
from voice_shopping_api.agents.nodes.constants import REQUIRED_SLOTS
from voice_shopping_api.agents.state import (
    ProductRecommendationResult,
    ShoppingState,
    ShoppingWorkflowContext,
)

logger = logging.getLogger(__name__)


def _score_product(
    product: dict[str, Any], state: ShoppingState, reranker_score: float | None = None
) -> tuple[float, dict[str, float]]:
    profile = state.get("user_profile_snapshot", {})
    static = profile.get("static", {})
    dynamic = profile.get("dynamic", {})
    category = str(product.get("category_l2", ""))
    brand = str(product.get("brand") or "")
    product_id = str(product.get("id", ""))
    facts = " ".join(
        [
            str(product.get("name", "")),
            str(product.get("description", "")),
            " ".join(product.get("selling_points", [])),
            str(product.get("attributes", {})),
        ]
    )
    keywords = [
        word for word in re.split(r"[，。\s、]+", state.get("utterance", "")) if len(word) >= 2
    ]
    lexical_hits = sum(1 for word in keywords if word in facts)
    reranker = (
        min(1.0, max(0.0, reranker_score))
        if reranker_score is not None
        else min(1.0, 0.52 + lexical_hits * 0.1)
    )
    dynamic_category = float(dynamic.get("categoryScores", {}).get(category, 0))
    dynamic_product = float(dynamic.get("productScores", {}).get(product_id, 0))
    dynamic_score = min(1.0, dynamic_category * 0.6 + dynamic_product * 0.4)
    static_category = float(static.get("categoryScores", {}).get(category, 0))
    static_brand = float(static.get("brandScores", {}).get(brand, 0))
    static_score = min(1.0, static_category * 0.6 + static_brand * 0.4)
    score = 0.4 * reranker + 0.4 * dynamic_score + 0.2 * static_score
    return score, {
        "reranker": round(reranker, 4),
        "dynamicProfile": round(dynamic_score, 4),
        "staticProfile": round(static_score, 4),
    }


def _attribute_matches(key: str, product_value: Any, requested_value: Any) -> bool:
    if product_value is None:
        return False
    if key == "gender" and product_value == "unisex":
        return requested_value in {"male", "female", "unisex"}
    if key == "size" and isinstance(product_value, list) and len(product_value) == 2:
        return float(product_value[0]) <= float(requested_value) <= float(product_value[1])
    if key in {"batteryHours", "pressureBar", "waterTankMl", "capacityL"}:
        return float(product_value) >= float(requested_value)
    if key == "waterResistance":
        matched = re.search(r"\d+", str(product_value))
        return bool(matched and int(matched.group()) >= int(requested_value))
    return (
        requested_value in product_value
        if isinstance(product_value, list)
        else product_value == requested_value
    )


def _product_attribute_value(attributes: dict[str, Any], slot: str) -> Any:
    return (
        attributes.get("size", attributes.get("sizeRange"))
        if slot == "size"
        else attributes.get(slot)
    )


async def recommend_products(state: ShoppingState) -> dict[str, Any]:
    intent = (state.get("intent") or {}).get("type")
    previous_cards = state.get("previous_product_cards", [])
    if intent in ("PRODUCT_COMPARE", "PRODUCT_QUERY") and previous_cards:
        selected = previous_cards
        if intent == "PRODUCT_QUERY":
            mentioned = [
                card
                for card in previous_cards
                if str(card.get("name") or "") in state.get("utterance", "")
            ]
            selected = mentioned or previous_cards[:1]
        return ProductRecommendationResult(
            product_cards=selected[:3], emotion_style="analytical-professional"
        ).model_dump()
    category = state.get("product_category")
    slots = state.get("slots", {})
    budget = slots.get("budgetMax")
    required_slots = state.get("required_slots_by_category", REQUIRED_SLOTS).get(category or "", [])
    products = []
    for product in state.get("catalog_products", []):
        if category and product.get("category_l2") != category:
            continue
        if budget is not None and Decimal(str(product.get("price", 0))) > Decimal(str(budget)):
            continue
        attributes = product.get("attributes", {})
        if any(
            not _attribute_matches(
                slot, _product_attribute_value(attributes, slot), slots.get(slot)
            )
            for slot in required_slots
            if slots.get(slot) is not None
        ):
            continue
        products.append(product)
    products = sorted(
        products, key=lambda product: float(product.get("vector_score") or 0), reverse=True
    )[:20]
    reranker_scores: dict[str, float] = {}
    if state.get("model_enabled") and products:
        try:
            reranker_scores = await rerank_products(state.get("utterance", ""), products)
        except Exception as exc:
            logger.warning("Reranker failed; using lexical fallback: %s", exc)
    ranked = sorted(
        (
            (*_score_product(product, state, reranker_scores.get(str(product.get("id")))), product)
            for product in products
        ),
        key=lambda item: (item[0], int(item[2].get("stock", 0))),
        reverse=True,
    )[:3]
    cards = [
        {
            "productId": str(product["id"]),
            "merchantId": str(product["merchant_id"]),
            "merchantName": product.get("merchant_name"),
            "name": product["name"],
            "brand": product.get("brand"),
            "price": float(product["price"]),
            "stock": product["stock"],
            "imageUrl": (product.get("image_urls") or [None])[0],
            "sellingPoints": product.get("selling_points", []),
            "attributes": product.get("attributes", {}),
            "matchScore": round(score, 4),
            "scoreBreakdown": score_parts,
        }
        for score, score_parts, product in ranked
    ]
    return ProductRecommendationResult(
        product_cards=cards, emotion_style="warm-professional" if cards else "helpful-apologetic"
    ).model_dump()


async def retrieve_catalog(
    state: ShoppingState, runtime: Runtime[ShoppingWorkflowContext]
) -> dict[str, Any]:
    """Fetch candidates only after the customer's required slots are complete."""
    context = runtime.context
    if context is None:
        return {"catalog_products": state.get("catalog_products", [])}
    return {
        "catalog_products": await context.catalog_loader(
            state.get("utterance", ""), bool(state.get("model_enabled"))
        )
    }
