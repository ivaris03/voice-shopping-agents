import logging
import re
from typing import Any

from voice_shopping_api.agents.model import recognize_with_model
from voice_shopping_api.agents.nodes.clarification import extract_slots_for_intent
from voice_shopping_api.agents.nodes.constants import CATEGORY_ALIASES
from voice_shopping_api.agents.state import IntentResult, ShoppingState

logger = logging.getLogger(__name__)

_CLAUSE_BOUNDARY = re.compile(r"[，。；！？、]|但是|不过|然而|而是|但")
_ASR_FILLERS = re.compile(r"[\s嗯啊呃额唔]+")
_NEGATION_MARKERS = (
    "不想要",
    "不想买",
    "不想下单",
    "不要下单",
    "不下单",
    "不要",
    "不买",
    "不需要",
    "算了",
    "别买",
    "别要",
    "别下单",
)


def _is_negated_at(utterance: str, position: int) -> bool:
    """Return whether the current clause contains a negation before ``position``."""
    prefix = utterance[:position]
    boundaries = list(_CLAUSE_BOUNDARY.finditer(prefix))
    clause_prefix = prefix[boundaries[-1].end() :] if boundaries else prefix
    normalized_prefix = _ASR_FILLERS.sub("", clause_prefix)
    return any(marker in normalized_prefix for marker in _NEGATION_MARKERS)


def _category(utterance: str) -> str | None:
    matches: list[tuple[int, str]] = []
    for category, aliases in CATEGORY_ALIASES.items():
        for alias in aliases:
            position = utterance.find(alias)
            if position >= 0:
                matches.append((position, category))
    if not matches:
        return None

    positive_matches = [match for match in matches if not _is_negated_at(utterance, match[0])]
    return min(positive_matches or matches, key=lambda match: match[0])[1]


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
    order_markers = (
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
    for marker in order_markers:
        start = utterance.find(marker)
        while start >= 0:
            if not _is_negated_at(utterance, start + len(marker)):
                return True
            start = utterance.find(marker, start + len(marker))
    return False


def _order_action(utterance: str, *, has_pending_order: bool) -> str:
    if any(
        word in utterance
        for word in (
            "取消",
            "不要了",
            "不买了",
            "不确认",
            "不要确认",
            "不下单",
            "不要下单",
        )
    ):
        return "CANCEL"
    if has_pending_order and any(word in utterance for word in ("确认", "确定", "就这样")):
        return "CONFIRM"
    return "CREATE"


def _starts_new_product_request(utterance: str, category: str | None, intent_type: str) -> bool:
    """Detect an explicit new purchase, excluding slot-answer phrasing."""
    if not category:
        return False
    if intent_type not in ("PRODUCT_RECOMMENDATION", "PRODUCT_ORDER"):
        # A model can call a noisy purchase transcript a query. Promote only
        # unambiguous purchase/recommendation language; short slot answers such
        # as "想要一个蓝牙的" are not new purchase requests.
        return any(marker in utterance for marker in ("买", "推荐", "帮我选", "帮我挑"))
    starts_new_request = any(
        marker in utterance for marker in ("买", "推荐", "帮我选", "帮我挑", "需要一", "要一")
    )
    return starts_new_request and not (
        intent_type == "PRODUCT_ORDER" and _has_explicit_order_request(utterance)
    )


def _starts_new_request_during_clarification(
    state: ShoppingState, utterance: str, category: str | None
) -> bool:
    """Separate a replacement shopping request from an answer to an open slot."""
    if not category:
        return False
    previous_category = _normalize_category(state.get("product_category"))
    if category != previous_category:
        # Naming a different product category abandons the outstanding question.
        return True
    return _starts_new_product_request(
        utterance, category, "PRODUCT_RECOMMENDATION"
    ) or any(marker in utterance for marker in ("重新", "换个", "换一个", "改买", "再推荐"))


def _requests_unspecified_category_switch(
    state: ShoppingState, utterance: str, category: str | None
) -> bool:
    """Detect a request to abandon the current category without naming a replacement."""
    current_category = _normalize_category(state.get("product_category"))
    if not current_category:
        return False
    if not any(
        marker in utterance
        for marker in (
            "另一个品类",
            "另外一个品类",
            "其他品类",
            "其它品类",
            "别的品类",
            "换个品类",
            "换一个品类",
            "另一个类别",
            "另外一个类别",
            "其他类别",
            "其它类别",
            "别的类别",
        )
    ):
        return False
    return not category or _normalize_category(category) == current_category


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
    if _requests_unspecified_category_switch(state, utterance, explicit_category):
        return await _finalize_intent_with_slots(
            state,
            IntentResult(
                type="PRODUCT_RECOMMENDATION",
                confidence=0.99,
            ).model_dump(exclude_none=True),
            True,
            clear_product_category=True,
        )
    selected_order = _selected_recommendation_order(state, utterance)
    if selected_order:
        return await _finalize_intent_with_slots(
            state, selected_order.model_dump(exclude_none=True), False
        )
    if state.get("pending_question"):
        if _starts_new_request_during_clarification(state, utterance, explicit_category):
            return await _finalize_intent_with_slots(
                state,
                IntentResult(
                    type="PRODUCT_RECOMMENDATION",
                    confidence=0.99,
                    product_category=explicit_category,
                ).model_dump(exclude_none=True),
                True,
            )
        return await _finalize_intent_with_slots(
            state,
            IntentResult(
                type="REQUIREMENT_CLARIFICATION",
                confidence=0.99,
            ).model_dump(exclude_none=True),
            False,
        )
    if state.get("model_enabled"):
        try:
            model_intent = await recognize_with_model(
                utterance,
                state.get("conversation_history", []),
                state.get("taxonomy_categories", []),
            )
            category = explicit_category or _normalize_category(model_intent.product_category)
            model_intent = model_intent.model_copy(update={"product_category": category})
            return await _finalize_intent_with_slots(
                state,
                model_intent.model_dump(exclude_none=True),
                _starts_new_product_request(utterance, category, model_intent.type),
            )
        except Exception as exc:
            logger.warning("Intent model failed; using deterministic fallback: %s", exc)
    detections: list[tuple[int, IntentResult]] = []

    def detect(keywords: tuple[str, ...], result: IntentResult) -> None:
        positions = [utterance.find(word) for word in keywords if word and word in utterance]
        if positions:
            detections.append((min(positions), result))

    comparison_words = ("对比", "比较", "区别", "比货", "多少钱", "库存", "介绍", "怎么样", "查询")
    recommendation_words = ("推荐", "想买", "帮我选", "需要一")
    if not any(word in utterance for word in comparison_words):
        recommendation_words += CATEGORY_ALIASES.get(explicit_category or "", ())
        if explicit_category in dynamic_category_names:
            recommendation_words += (dynamic_category_names[explicit_category],)
    detect(
        recommendation_words,
        IntentResult(
            type="PRODUCT_RECOMMENDATION", confidence=0.95, product_category=explicit_category
        ),
    )
    detect(
        comparison_words,
        IntentResult(type="PRODUCT_COMPARE", confidence=0.94, product_category=explicit_category),
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
    return await _finalize_intent_with_slots(
        state,
        selected.model_dump(exclude_none=True),
        _starts_new_product_request(utterance, selected.product_category, selected.type),
    )


def _finalize_intent(
    state: ShoppingState,
    recognized_intent: dict[str, Any],
    starts_new_product_request: bool,
    *,
    clear_product_category: bool = False,
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
        "category_changed": clear_product_category
        or bool(category and category != previous_category),
    }
    if clear_product_category:
        updates["product_category"] = None
    elif category:
        intent["product_category"] = category
        updates["product_category"] = category

    # A clear new purchase request is more reliable than a model's compare/chat
    # label for noisy ASR. Selected checkout requests were handled above, so
    # routing this case through clarification cannot steal a concrete order.
    if starts_new_product_request and intent.get("type") != "PRODUCT_RECOMMENDATION":
        intent = IntentResult(
            type="PRODUCT_RECOMMENDATION",
            confidence=float(intent.get("confidence", 0.0)),
            product_category=category,
        ).model_dump(exclude_none=True)

    # New recommendation turns and category switches must not inherit filters
    # or a question that belonged to an earlier shopping request.
    if starts_new_product_request or updates["category_changed"]:
        updates["slots"] = {}
        updates["pending_question"] = None
        updates["product_cards"] = []
        updates["previous_product_cards"] = []

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


async def _finalize_intent_with_slots(
    state: ShoppingState,
    recognized_intent: dict[str, Any],
    starts_new_product_request: bool,
    *,
    clear_product_category: bool = False,
) -> dict[str, Any]:
    """Finalize intent and let the intent Agent own the slot-state update."""
    updates = _finalize_intent(
        state,
        recognized_intent,
        starts_new_product_request,
        clear_product_category=clear_product_category,
    )
    intent_type = (updates.get("intent") or {}).get("type")
    if intent_type not in ("PRODUCT_RECOMMENDATION", "REQUIREMENT_CLARIFICATION"):
        return updates

    projected_state: ShoppingState = {**state, **updates}
    updates["slots"] = await extract_slots_for_intent(projected_state)
    return updates
