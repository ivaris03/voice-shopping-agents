import logging
import re
from decimal import Decimal
from typing import Any

from langgraph.graph import END, START, StateGraph

from voice_shopping_api.agents.model import (
    recognize_with_model,
    rerank_products,
    respond_with_model,
)
from voice_shopping_api.agents.state import (
    ClarificationResult,
    EmotionalResponseResult,
    IntentResult,
    ProductReason,
    ProductRecommendationResult,
    ShoppingState,
)

logger = logging.getLogger(__name__)

REQUIRED_SLOTS: dict[str, list[str]] = {
    "HEADPHONES": ["budgetMax", "useCase"],
    "COFFEE_MACHINE": ["budgetMax", "useCase"],
    "RUNNING_SHOES": ["budgetMax", "useCase"],
    "WATCHES": ["budgetMax", "style"],
    "LIPSTICK": ["budgetMax", "colorPreference"],
}

CATEGORY_ALIASES: dict[str, tuple[str, ...]] = {
    "HEADPHONES": ("耳机", "蓝牙耳机", "降噪耳机"),
    "COFFEE_MACHINE": ("咖啡机", "胶囊机"),
    "RUNNING_SHOES": ("跑鞋", "跑步鞋", "运动鞋"),
    "WATCHES": ("手表", "腕表", "表"),
    "LIPSTICK": ("口红", "唇膏"),
}

QUESTIONS = {
    "productCategory": "你想购买哪一类商品？",
    "budgetMax": "你的预算上限是多少？",
    "useCase": "主要用于什么场景？",
    "style": "你更偏好什么风格？",
    "colorPreference": "你更喜欢什么颜色或妆效？",
}

COMPLIANCE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (r"百分百", r"绝对(?:有效|安全)", r"包治", r"国家级", r"稳赚不赔")
)
COMPLIANCE_FALLBACK = "抱歉，这段推荐话术未通过合规检查。你可以查看商品事实后再做选择。"


def _category(utterance: str) -> str | None:
    for category, aliases in CATEGORY_ALIASES.items():
        if any(alias in utterance for alias in aliases):
            return category
    return None


def _order_action(utterance: str) -> str:
    if any(word in utterance for word in ("取消", "不要了", "不买了")):
        return "CANCEL"
    if any(word in utterance for word in ("确认", "确定", "下单吧", "就这样")):
        return "CONFIRM"
    return "CREATE"


async def recognize_intent(state: ShoppingState) -> dict[str, Any]:
    utterance = state.get("utterance", "").strip()
    category = _category(utterance) or state.get("product_category")
    if state.get("model_enabled") and not state.get("pending_question"):
        try:
            model_results = await recognize_with_model(
                utterance, state.get("conversation_history", [])
            )
            if model_results:
                normalized_results: list[IntentResult] = []
                for item in model_results:
                    normalized_category = item.product_category
                    if normalized_category:
                        normalized_category = normalized_category.upper()
                        for canonical, aliases in CATEGORY_ALIASES.items():
                            if item.product_category in aliases:
                                normalized_category = canonical
                                break
                    normalized_results.append(
                        item.model_copy(update={"product_category": normalized_category})
                    )
                model_results = normalized_results
                model_category = next(
                    (item.product_category for item in model_results if item.product_category),
                    category,
                )
                model_category = category or model_category
                return {
                    "intents": [item.model_dump(exclude_none=True) for item in model_results],
                    "action_queue": [item.type for item in model_results],
                    "product_category": model_category,
                }
        except Exception as exc:
            logger.warning("Intent model failed; using deterministic fallback: %s", exc)
    if state.get("pending_question"):
        results = [
            IntentResult(type="PRODUCT_RECOMMENDATION", confidence=0.99, product_category=category)
        ]
    else:
        detections: list[tuple[int, IntentResult]] = []

        def detect(keywords: tuple[str, ...], result: IntentResult) -> None:
            positions = [utterance.find(word) for word in keywords if word and word in utterance]
            if positions:
                detections.append((min(positions), result))

        recommendation_words = ("推荐", "想买", "帮我选", "需要一") + CATEGORY_ALIASES.get(
            category or "", ()
        )
        detect(
            recommendation_words,
            IntentResult(type="PRODUCT_RECOMMENDATION", confidence=0.95, product_category=category),
        )
        detect(
            ("对比", "比较", "区别"),
            IntentResult(type="PRODUCT_COMPARE", confidence=0.94, product_category=category),
        )
        detect(
            ("多少钱", "库存", "介绍", "怎么样", "查询"),
            IntentResult(type="PRODUCT_QUERY", confidence=0.9, product_category=category),
        )
        detect(
            ("下单", "买第一", "买第二", "买第三", "确认", "取消订单"),
            IntentResult(type="PRODUCT_ORDER", confidence=0.97, action=_order_action(utterance)),
        )
        detect(("你好", "谢谢", "嗨", "再见"), IntentResult(type="CHAT", confidence=0.9))
        if not detections:
            detections.append((0, IntentResult(type="UNSUPPORTED_REQUEST", confidence=0.86)))
        results = []
        seen: set[str] = set()
        for _, result in sorted(detections, key=lambda item: item[0]):
            if result.type not in seen:
                results.append(result)
                seen.add(result.type)
    data = [result.model_dump(exclude_none=True) for result in results]
    return {
        "intents": data,
        "action_queue": [result.type for result in results],
        "product_category": category,
    }


def _chinese_amount(value: str) -> int | None:
    simple = {
        "五百": 500,
        "六百": 600,
        "八百": 800,
        "一千": 1000,
        "两千": 2000,
        "三千": 3000,
        "五千": 5000,
        "一万": 10000,
    }
    return next((amount for text, amount in simple.items() if text in value), None)


def _extract_slots(utterance: str, slots: dict[str, Any]) -> dict[str, Any]:
    updated = dict(slots)
    number = re.search(r"(?<!\d)(\d{2,6})(?:\s*元)?(?:以内|以下|左右|预算)?", utterance)
    amount = int(number.group(1)) if number else _chinese_amount(utterance)
    if amount and any(
        word in utterance for word in ("预算", "以内", "以下", "元", "百", "千", "万")
    ):
        updated["budgetMax"] = amount
    use_cases = {
        "commute": ("通勤", "地铁", "上班"),
        "daily-road-running": ("路跑", "日常跑", "跑步训练", "慢跑"),
        "trail-running": ("越野", "山路"),
        "home": ("家用", "在家", "家庭"),
        "office": ("办公室", "办公"),
    }
    for canonical, aliases in use_cases.items():
        if any(alias in utterance for alias in aliases):
            updated["useCase"] = canonical
            break
    if "降噪" in utterance:
        updated["noiseCancellation"] = True
    for style in ("商务", "运动", "复古", "简约", "休闲"):
        if style in utterance:
            updated["style"] = style
    for color in ("红色", "粉色", "豆沙", "橘色", "哑光", "水光"):
        if color in utterance:
            updated["colorPreference"] = color
    return updated


async def clarify_requirements(state: ShoppingState) -> dict[str, Any]:
    category = state.get("product_category")
    slots = _extract_slots(state.get("utterance", ""), state.get("slots", {}))
    if not category:
        result = ClarificationResult(
            status="ASK",
            slots=slots,
            missing_slots=["productCategory"],
            question=QUESTIONS["productCategory"],
        )
        required_slots: list[str] = []
        question_slot = "productCategory"
    else:
        required_slots = REQUIRED_SLOTS.get(category, ["budgetMax", "useCase"])
        missing = [slot for slot in required_slots if slot not in slots]
        result = ClarificationResult(
            status="ASK" if missing else "READY",
            slots=slots,
            missing_slots=missing,
            question=QUESTIONS.get(missing[0]) if missing else None,
        )
        question_slot = missing[0] if missing else ""
    return {
        "required_slots": required_slots,
        "slots": result.slots,
        "clarification_status": result.status,
        "missing_slots": result.missing_slots,
        "pending_question": (
            {"slot": question_slot, "question": result.question or ""}
            if result.status == "ASK"
            else None
        ),
    }


def _score_product(
    product: dict[str, Any], state: ShoppingState, reranker_score: float | None = None
) -> tuple[float, dict[str, float]]:
    profile = state.get("user_profile_snapshot", {})
    static = profile.get("static", {})
    dynamic = profile.get("dynamic", {})
    category = str(product.get("category_l2", ""))
    brand = str(product.get("brand") or "")
    product_id = str(product.get("id", ""))
    utterance = state.get("utterance", "")
    facts = " ".join(
        [
            str(product.get("name", "")),
            str(product.get("description", "")),
            " ".join(product.get("selling_points", [])),
            str(product.get("attributes", {})),
        ]
    )
    keywords = [word for word in re.split(r"[，。\s、]+", utterance) if len(word) >= 2]
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


async def recommend_products(state: ShoppingState) -> dict[str, Any]:
    intent = (state.get("intents") or [{}])[0].get("type")
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
        result = ProductRecommendationResult(
            product_cards=selected[:3],
            emotion_style="analytical-professional",
        )
        return result.model_dump()
    category = state.get("product_category")
    budget = state.get("slots", {}).get("budgetMax")
    products = []
    for product in state.get("catalog_products", []):
        if category and product.get("category_l2") != category:
            continue
        if budget is not None and Decimal(str(product.get("price", 0))) > Decimal(str(budget)):
            continue
        attributes = product.get("attributes", {})
        if state.get("slots", {}).get("noiseCancellation") and not attributes.get(
            "noiseCancellation"
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
            (
                *_score_product(product, state, reranker_scores.get(str(product.get("id")))),
                product,
            )
            for product in products
        ),
        key=lambda item: (item[0], int(item[2].get("stock", 0))),
        reverse=True,
    )[:3]
    cards: list[dict[str, Any]] = []
    for score, score_parts, product in ranked:
        cards.append(
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
        )
    result = ProductRecommendationResult(
        product_cards=cards,
        emotion_style="warm-professional" if cards else "helpful-apologetic",
    )
    return result.model_dump()


async def order_response(_: ShoppingState) -> dict[str, Any]:
    return {"speech_text": "正在处理你的订单请求。", "final_reply": "正在处理你的订单请求。"}


async def emotional_response(state: ShoppingState) -> dict[str, Any]:
    if state.get("clarification_status") == "ASK":
        speech = (state.get("pending_question") or {}).get("question", QUESTIONS["productCategory"])
        result = EmotionalResponseResult(reasons=[], speech_text=speech)
    elif state.get("product_cards"):
        if state.get("model_enabled"):
            try:
                model_result = await respond_with_model(
                    state.get("utterance", ""),
                    state["product_cards"],
                    state.get("emotion_style", "warm-professional"),
                )
                return {
                    "reasons": [reason.model_dump() for reason in model_result.reasons],
                    "speech_text": model_result.speech_text,
                    "final_reply": model_result.speech_text,
                }
            except Exception as exc:
                logger.warning("Response model failed; using deterministic fallback: %s", exc)
        reasons = []
        for index, card in enumerate(state["product_cards"], start=1):
            point = (card.get("sellingPoints") or ["整体匹配你的需求"])[0]
            reasons.append(
                ProductReason(
                    product_id=card["productId"],
                    reason=f"第{index}款{card['name']}：{point}，且价格符合当前筛选条件。",
                )
            )
        speech = "我筛选出了三款商品。" if len(reasons) == 3 else f"我找到了{len(reasons)}款商品。"
        speech += " ".join(reason.reason for reason in reasons)
        result = EmotionalResponseResult(reasons=reasons, speech_text=speech)
    else:
        intent = (state.get("intents") or [{}])[0].get("type")
        if intent == "CHAT":
            speech = "你好，我可以帮你推荐、查询、对比商品，也可以协助语音下单。"
        elif intent == "UNSUPPORTED_REQUEST":
            speech = "抱歉，我目前只能协助商品推荐、查询、对比和下单。你可以告诉我想买什么商品。"
        else:
            speech = "暂时没有找到符合条件的在售商品，可以放宽预算或换一个品类试试。"
        result = EmotionalResponseResult(reasons=[], speech_text=speech)
    return {
        "reasons": [reason.model_dump() for reason in result.reasons],
        "speech_text": result.speech_text,
        "final_reply": result.speech_text,
    }


def is_compliant(text_value: str) -> bool:
    return not any(pattern.search(text_value) for pattern in COMPLIANCE_PATTERNS)


async def compliance_check(state: ShoppingState) -> dict[str, Any]:
    speech = state.get("speech_text", "")
    if is_compliant(speech):
        return {"compliance_blocked": False, "final_reply": speech}
    return {
        "compliance_blocked": True,
        "reasons": [],
        "speech_text": COMPLIANCE_FALLBACK,
        "final_reply": COMPLIANCE_FALLBACK,
    }


def _route_intent(state: ShoppingState) -> str:
    intent = (state.get("intents") or [{}])[0].get("type")
    if intent == "PRODUCT_RECOMMENDATION":
        return "clarify"
    if intent in ("PRODUCT_COMPARE", "PRODUCT_QUERY"):
        return "recommend"
    if intent == "PRODUCT_ORDER":
        return "order"
    return "respond"


def _route_clarification(state: ShoppingState) -> str:
    return "recommend" if state.get("clarification_status") == "READY" else "respond"


def build_workflow():
    graph = StateGraph(ShoppingState)
    graph.add_node("intent_agent", recognize_intent)
    graph.add_node("clarification_agent", clarify_requirements)
    graph.add_node("recommendation_agent", recommend_products)
    graph.add_node("order_node", order_response)
    graph.add_node("emotional_agent", emotional_response)
    graph.add_node("compliance_check", compliance_check)
    graph.add_edge(START, "intent_agent")
    graph.add_conditional_edges(
        "intent_agent",
        _route_intent,
        {
            "clarify": "clarification_agent",
            "recommend": "recommendation_agent",
            "order": "order_node",
            "respond": "emotional_agent",
        },
    )
    graph.add_conditional_edges(
        "clarification_agent",
        _route_clarification,
        {"recommend": "recommendation_agent", "respond": "emotional_agent"},
    )
    graph.add_edge("recommendation_agent", "emotional_agent")
    graph.add_edge("order_node", "compliance_check")
    graph.add_edge("emotional_agent", "compliance_check")
    graph.add_edge("compliance_check", END)
    return graph.compile()


shopping_workflow = build_workflow()
