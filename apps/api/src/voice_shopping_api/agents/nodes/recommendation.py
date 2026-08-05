import logging
import re
from typing import Any

from langgraph.runtime import Runtime

from voice_shopping_api.agents.model import rerank_products
from voice_shopping_api.agents.nodes.constants import REQUIRED_SLOTS
from voice_shopping_api.agents.state import (
    CatalogFilters,
    ProductRecommendationResult,
    ShoppingRuntimeDependencies,
    ShoppingState,
)

logger = logging.getLogger(__name__)

# 画像快照规则二次排序的调整分：品牌命中加分，超价/复购扣分。
RULE_BRAND_HIT = 0.2
RULE_PRICE_OVER_AVG = -0.15
RULE_REPEAT_PURCHASE = -0.3
AVG_ORDER_AMOUNT_MULTIPLIER = 1.5


def _serialize_timestamp(value: object) -> str:
    if value is None:
        return ""
    isoformat = getattr(value, "isoformat", None)
    return str(isoformat()) if callable(isoformat) else str(value)


def _reranker_score(
    product: dict[str, Any],
    state: ShoppingState,
    reranker_scores: dict[str, float],
) -> float:
    """LLM TextReRank 分；模型不可用或失败时退回词法命中兜底。"""
    score = reranker_scores.get(str(product.get("id")))
    if score is not None:
        return min(1.0, max(0.0, score))
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
    return min(1.0, 0.52 + lexical_hits * 0.1)


def _rule_adjustments(
    product: dict[str, Any], profile: dict[str, Any]
) -> tuple[float, dict[str, float]]:
    """画像快照规则：动态品牌偏好加分，超出平均客单价或复购则扣分。"""
    dynamic = profile.get("dynamic", {})
    brand = str(product.get("brand") or "")
    parts: dict[str, float] = {}
    if float(dynamic.get("brandAffinity", {}).get(brand, 0)) > 0:
        parts["brandHit"] = RULE_BRAND_HIT
    avg_order_amount = dynamic.get("avgOrderAmount")
    if (
        avg_order_amount is not None
        and float(product.get("price", 0))
        > float(avg_order_amount) * AVG_ORDER_AMOUNT_MULTIPLIER
    ):
        parts["priceOverAvgOrderAmount"] = RULE_PRICE_OVER_AVG
    if str(product.get("id", "")) in dynamic.get("recentPurchased", []):
        parts["repeatPurchase"] = RULE_REPEAT_PURCHASE
    return sum(parts.values()), parts


def _match_score(rule_score: float, reranker: float) -> float:
    """Return the public match score, capped at 100 percent."""
    return round(min(1.0, rule_score + reranker), 4)


async def recommend_products(
    state: ShoppingState, runtime: Runtime[ShoppingRuntimeDependencies]
) -> dict[str, Any]:
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
    # SQL 已按已填槽位过滤并按向量相似度截断为最多 20 条。
    products = await _retrieve_catalog(state, runtime)
    reranker_scores: dict[str, float] = {}
    if state.get("model_enabled") and products:
        try:
            reranker_scores = await rerank_products(state.get("utterance", ""), products)
        except Exception as exc:
            logger.warning("Reranker failed; using lexical fallback: %s", exc)
    # 第一阶段：reranker 精排，取 3 个候选。
    rerank_ranked = sorted(
        (
            (product, _reranker_score(product, state, reranker_scores))
            for product in products
        ),
        key=lambda item: (item[1], int(item[0].get("stock", 0))),
        reverse=True,
    )[:3]
    # 第二阶段：画像快照规则（品牌偏好 / 平均客单价 / 最近购买）二次排序。
    # 规则分相同（parts 必相同）时保持第一阶段顺序，即稳定排序。
    profile = state.get("user_profile_snapshot", {})
    ranked = sorted(
        (
            (*_rule_adjustments(product, profile), reranker, product)
            for product, reranker in rerank_ranked
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    cards = [
        {
            "productId": str(product["id"]),
            "merchantId": str(product["merchant_id"]),
            "merchantName": product.get("merchant_name"),
            "sku": product.get("sku"),
            "name": product["name"],
            "categoryL1": product.get("category_l1", ""),
            "categoryL2": product.get("category_l2", ""),
            "brand": product.get("brand"),
            "description": product.get("description", ""),
            "price": float(product["price"]),
            "stock": product["stock"],
            "imageUrl": (product.get("image_urls") or [None])[0],
            "imageUrls": product.get("image_urls", []),
            "status": product.get("status", "on_sale"),
            "createdAt": _serialize_timestamp(product.get("created_at")),
            "updatedAt": _serialize_timestamp(product.get("updated_at")),
            "sellingPoints": product.get("selling_points", []),
            "attributes": product.get("attributes", {}),
            "matchScore": _match_score(rule_score, reranker),
            "scoreBreakdown": {"reranker": round(reranker, 4), **rule_parts},
        }
        for rule_score, rule_parts, reranker, product in ranked
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
        slots = {
            slot: value
            for slot, value in slots.items()
            if slot in allowed_keys
        }
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
