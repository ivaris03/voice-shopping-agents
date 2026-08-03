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
    cards = state.get("previous_product_cards") or state.get("product_cards") or []
    if not cards:
        return False
    if any(
        marker in utterance
        for marker in ("下单", "第一", "第二", "第三", "这款", "那款", "就要", "就买")
    ):
        return True
    return any(card.get("name") and str(card["name"]) in utterance for card in cards)


def _has_recommendation_cards(state: ShoppingState) -> bool:
    """Orders may only be created after this conversation has shown products."""
    return bool(state.get("previous_product_cards") or state.get("product_cards"))


def _has_pending_order(state: ShoppingState) -> bool:
    pending_order = state.get("pending_order") or {}
    return bool(pending_order.get("id")) and pending_order.get("status", "pending") == "pending"


def _has_explicit_order_request(utterance: str) -> bool:
    return any(
        marker in utterance
        for marker in (
            "下单",
            "买第一",
            "买第二",
            "买第三",
            "买这个",
            "买这款",
            "就买",
            "就要",
            "帮我买",
            "取消订单",
        )
    )


def _order_action(utterance: str, *, has_pending_order: bool) -> str:
    if any(word in utterance for word in ("取消", "不要了", "不买了")):
        return "CANCEL"
    if has_pending_order and any(word in utterance for word in ("确认", "确定", "就这样")):
        return "CONFIRM"
    return "CREATE"


def _starts_new_product_request(utterance: str, category: str | None, intent_type: str) -> bool:
    """Detect an explicit new purchase, excluding slot-answer phrasing."""
    if not category or intent_type not in ("PRODUCT_RECOMMENDATION", "PRODUCT_ORDER"):
        return False
    starts_new_request = any(
        marker in utterance for marker in ("买", "推荐", "帮我选", "帮我挑", "需要一", "要一")
    )
    return starts_new_request and not (
        intent_type == "PRODUCT_ORDER" and _has_explicit_order_request(utterance)
    )


def _selected_recommendation_order(state: ShoppingState, utterance: str) -> IntentResult | None:
    """Prefer a concrete checkout instruction over category-word recommendations."""
    has_order_context = _has_recommendation_cards(state) or _has_pending_order(state)
    if not has_order_context or not _has_explicit_order_request(utterance):
        return None
    return IntentResult(
        type="PRODUCT_ORDER",
        confidence=0.99,
        action=_order_action(utterance, has_pending_order=_has_pending_order(state)),
    )


async def recognize_intent(state: ShoppingState) -> dict[str, Any]:
    """Recognize and finalize the current turn's intent in one node.

    Classification remains fresh for every turn.  The returned update also
    contains deterministic category normalization and order-safety guards so
    the router sees the final intent without a second graph node.
    """
    utterance = state.get("utterance", "").strip()
    dynamic_category_names = state.get("taxonomy_category_names", {})
    explicit_category = next(
        (code for code, name in dynamic_category_names.items() if name and name in utterance),
        None,
    ) or _category(utterance)
    selected_order = _selected_recommendation_order(state, utterance)
    if selected_order:
        return _finalize_intent(state, selected_order.model_dump(exclude_none=True), False)
    if state.get("model_enabled"):
        try:
            model_intent = await recognize_with_model(
                utterance,
                state.get("conversation_history", []),
                state.get("taxonomy_categories", []),
            )
            category = explicit_category or _normalize_category(model_intent.product_category)
            model_intent = model_intent.model_copy(update={"product_category": category})
            return _finalize_intent(
                state,
                model_intent.model_dump(exclude_none=True),
                _starts_new_product_request(utterance, explicit_category, model_intent.type),
            )
        except Exception as exc:
            logger.warning("Intent model failed; using deterministic fallback: %s", exc)
    detections: list[tuple[int, IntentResult]] = []

    def detect(keywords: tuple[str, ...], result: IntentResult) -> None:
        positions = [utterance.find(word) for word in keywords if word and word in utterance]
        if positions:
            detections.append((min(positions), result))

    recommendation_words = ("推荐", "想买", "帮我选", "需要一") + CATEGORY_ALIASES.get(
        explicit_category or "", ()
    )
    if explicit_category in dynamic_category_names:
        recommendation_words += (dynamic_category_names[explicit_category],)
    detect(
        recommendation_words,
        IntentResult(
            type="PRODUCT_RECOMMENDATION", confidence=0.95, product_category=explicit_category
        ),
    )
    detect(
        ("对比", "比较", "区别"),
        IntentResult(type="PRODUCT_COMPARE", confidence=0.94, product_category=explicit_category),
    )
    detect(
        ("多少钱", "库存", "介绍", "怎么样", "查询"),
        IntentResult(type="PRODUCT_QUERY", confidence=0.9, product_category=explicit_category),
    )
    detect(
        ("下单", "买第一", "买第二", "买第三", "确认", "取消订单"),
        IntentResult(
            type="PRODUCT_ORDER",
            confidence=0.97,
            action=_order_action(utterance, has_pending_order=_has_pending_order(state)),
        ),
    )
    detect(("你好", "谢谢", "嗨", "再见"), IntentResult(type="CHAT", confidence=0.9))
    if not detections:
        detections.append((0, IntentResult(type="UNSUPPORTED_REQUEST", confidence=0.86)))
    selected = min(detections, key=lambda item: item[0])[1]
    return _finalize_intent(
        state,
        selected.model_dump(exclude_none=True),
        _starts_new_product_request(utterance, explicit_category, selected.type),
    )


def _finalize_intent(
    state: ShoppingState,
    recognized_intent: dict[str, Any],
    starts_new_product_request: bool,
) -> dict[str, Any]:
    """Normalize the recognized intent and apply deterministic safety guards.

    This is a pure helper used by ``recognize_intent``; it is deliberately not
    a separate graph node.  State merging and checkpointing remain the
    responsibility of LangGraph.
    """
    intent = dict(recognized_intent)
    category = _normalize_category(intent.get("product_category"))
    previous_category = _normalize_category(state.get("product_category"))
    updates: dict[str, Any] = {
        "starts_new_product_request": starts_new_product_request,
        "category_changed": bool(category and category != previous_category),
    }
    if category:
        intent["product_category"] = category
        updates["product_category"] = category

    if (
        intent.get("type") == "PRODUCT_ORDER"
        and intent.get("action") == "CREATE"
        and (
            (
                starts_new_product_request
                and not _has_order_target(state, state.get("utterance", ""))
            )
            or not _has_recommendation_cards(state)
            or (
                updates["category_changed"]
                and not _has_order_target(state, state.get("utterance", ""))
            )
        )
    ):
        intent = IntentResult(
            type="PRODUCT_RECOMMENDATION",
            confidence=float(intent.get("confidence", 0.0)),
            product_category=category,
        ).model_dump(exclude_none=True)
        updates["starts_new_product_request"] = bool(
            category or starts_new_product_request
        )

    updates["intent"] = intent
    return updates
