from typing import Any

from langgraph.runtime import Runtime

from voice_shopping_api.agents.nodes.constants import REQUIRED_SLOTS
from voice_shopping_api.agents.profile_reranker import ProfileReranker
from voice_shopping_api.agents.state import (
    CatalogFilters,
    ProductRecommendationResult,
    ShoppingRuntimeDependencies,
    ShoppingState,
)


def _serialize_timestamp(value: object) -> str:
    if value is None:
        return ""
    isoformat = getattr(value, "isoformat", None)
    return str(isoformat()) if callable(isoformat) else str(value)


async def recommend_products(
    state: ShoppingState, runtime: Runtime[ShoppingRuntimeDependencies]
) -> dict[str, Any]:
    intent = (state.get("intent") or {}).get("type")
    previous_cards = state.get("previous_product_cards", [])
    if intent == "PRODUCT_COMPARE" and previous_cards:
        selected = previous_cards
        utterance = state.get("utterance", "")
        comparison_words = ("对比", "比较", "区别", "比货")
        single_product_query_words = ("多少钱", "库存", "介绍", "怎么样", "查询")
        if any(word in utterance for word in single_product_query_words) and not any(
            word in utterance for word in comparison_words
        ):
            mentioned = [
                card for card in previous_cards if str(card.get("name") or "") in utterance
            ]
            selected = mentioned or previous_cards[:1]
        return ProductRecommendationResult(
            product_cards=selected[:3], emotion_style="analytical-professional"
        ).model_dump()
    # SQL 已按 JSONB 槽位过滤并按 PGVector 相似度截断为最多 20 条。
    products = await _retrieve_catalog(state, runtime)
    # ProfileReranker 必须看到完整候选池，之后才允许截断 Top 3。
    ranked = ProfileReranker().rerank(products, state.get("user_profile_snapshot", {}))
    cards = [
        {
            "productId": str(item.product["id"]),
            "merchantId": str(item.product["merchant_id"]),
            "merchantName": item.product.get("merchant_name"),
            "sku": item.product.get("sku"),
            "name": item.product["name"],
            "categoryL1": item.product.get("category_l1", ""),
            "categoryL2": item.product.get("category_l2", ""),
            "brand": item.product.get("brand"),
            "description": item.product.get("description", ""),
            "price": float(item.product["price"]),
            "stock": item.product["stock"],
            "imageUrl": (item.product.get("image_urls") or [None])[0],
            "imageUrls": item.product.get("image_urls", []),
            "status": item.product.get("status", "on_sale"),
            "createdAt": _serialize_timestamp(item.product.get("created_at")),
            "updatedAt": _serialize_timestamp(item.product.get("updated_at")),
            "sellingPoints": item.product.get("selling_points", []),
            "attributes": item.product.get("attributes", {}),
            "matchScore": item.match_score,
            "scoreBreakdown": item.score_breakdown,
        }
        for item in ranked
    ]
    result = ProductRecommendationResult(
        product_cards=cards, emotion_style="warm-professional" if cards else "helpful-apologetic"
    ).model_dump()
    return {"catalog_products": products, **result}


async def _retrieve_catalog(
    state: ShoppingState, runtime: Runtime[ShoppingRuntimeDependencies]
) -> list[dict[str, Any]]:
    """Fetch candidates for the recommendation node's ranking phase."""
    context = runtime.context
    if context is None:
        return state.get("catalog_products", [])
    category = state.get("product_category")
    slots = state.get("slots", {})
    allowed_by_category = state.get("allowed_slots_by_category")
    allowed_slots = (
        allowed_by_category.get(category)
        if isinstance(allowed_by_category, dict) and category
        else None
    )
    if allowed_slots is None:
        allowed_slots = state.get("allowed_slots")
    if isinstance(allowed_slots, list):
        allowed_keys = {*allowed_slots, "budgetMax"}
        slots = {slot: value for slot, value in slots.items() if slot in allowed_keys}
    filters: CatalogFilters = {
        "category": category,
        "slots": slots,
        "required_slots": state.get("required_slots_by_category", REQUIRED_SLOTS).get(
            category or "", []
        ),
    }
    return await context.catalog_loader(
        state.get("utterance", ""), bool(state.get("model_enabled")), filters
    )
