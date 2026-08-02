import logging
from typing import Any

from voice_shopping_api.agents.model import recognize_with_model
from voice_shopping_api.agents.nodes.constants import CATEGORY_ALIASES
from voice_shopping_api.agents.state import IntentResult, ShoppingState

logger = logging.getLogger(__name__)


def _category(utterance: str) -> str | None:
    for category, aliases in CATEGORY_ALIASES.items():
        if any(alias in utterance for alias in aliases):
            return category
    return None


def _normalize_category(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip()
    canonical = normalized.upper()
    if canonical in CATEGORY_ALIASES:
        return canonical
    return _category(normalized)


def _has_order_target(state: ShoppingState, utterance: str) -> bool:
    if any(
        marker in utterance
        for marker in ("下单", "第一", "第二", "第三", "这款", "那款", "就要", "就买")
    ):
        return True
    cards = state.get("previous_product_cards") or state.get("product_cards") or []
    return any(card.get("name") and str(card["name"]) in utterance for card in cards)


def _order_action(utterance: str) -> str:
    if any(word in utterance for word in ("取消", "不要了", "不买了")):
        return "CANCEL"
    if any(word in utterance for word in ("确认", "确定", "下单吧", "就这样")):
        return "CONFIRM"
    return "CREATE"


async def recognize_intent(state: ShoppingState) -> dict[str, Any]:
    utterance = state.get("utterance", "").strip()
    previous_category = _normalize_category(state.get("product_category"))
    dynamic_category_names = state.get("taxonomy_category_names", {})
    explicit_category = next(
        (code for code, name in dynamic_category_names.items() if name and name in utterance),
        None,
    ) or _category(utterance)
    category_switched_by_rule = bool(explicit_category and explicit_category != previous_category)
    if state.get("model_enabled") and (
        not state.get("pending_question") or category_switched_by_rule
    ):
        try:
            model_intent = await recognize_with_model(
                utterance,
                state.get("conversation_history", []),
                state.get("taxonomy_categories", []),
            )
            normalized_category = (
                model_intent.product_category
                if model_intent.product_category in dynamic_category_names
                else _normalize_category(model_intent.product_category)
            )
            model_intent = model_intent.model_copy(update={"product_category": normalized_category})
            category = explicit_category or model_intent.product_category or previous_category
            category_changed = bool(category and category != previous_category)
            if (
                category_changed
                and model_intent.type == "PRODUCT_ORDER"
                and model_intent.action == "CREATE"
                and not _has_order_target(state, utterance)
            ):
                model_intent = IntentResult(
                    type="PRODUCT_RECOMMENDATION",
                    confidence=model_intent.confidence,
                    product_category=category,
                )
            elif category and model_intent.type in (
                "PRODUCT_RECOMMENDATION",
                "PRODUCT_COMPARE",
                "PRODUCT_QUERY",
            ):
                model_intent = model_intent.model_copy(update={"product_category": category})
            return {
                "intent": model_intent.model_dump(exclude_none=True),
                "product_category": category,
                "category_changed": category_changed,
            }
        except Exception as exc:
            logger.warning("Intent model failed; using deterministic fallback: %s", exc)
    category = explicit_category or previous_category
    category_changed = bool(category and category != previous_category)
    if state.get("pending_question") and not category_changed:
        selected = IntentResult(
            type="PRODUCT_RECOMMENDATION", confidence=0.99, product_category=category
        )
    else:
        detections: list[tuple[int, IntentResult]] = []

        def detect(keywords: tuple[str, ...], result: IntentResult) -> None:
            positions = [utterance.find(word) for word in keywords if word and word in utterance]
            if positions:
                detections.append((min(positions), result))

        recommendation_words = ("推荐", "想买", "帮我选", "需要一") + CATEGORY_ALIASES.get(
            category or "", ()
        )
        if category in dynamic_category_names:
            recommendation_words += (dynamic_category_names[category],)
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
        selected = min(detections, key=lambda item: item[0])[1]
    return {
        "intent": selected.model_dump(exclude_none=True),
        "product_category": category,
        "category_changed": category_changed,
    }
