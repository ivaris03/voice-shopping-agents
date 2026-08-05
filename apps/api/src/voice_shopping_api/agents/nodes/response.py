import asyncio
import logging
import math
import re
from typing import Any

from langgraph.runtime import Runtime

from voice_shopping_api.agents.model import (
    generate_product_reason,
    generate_recommendation_hook,
)
from voice_shopping_api.agents.nodes.constants import (
    COMPLIANCE_FALLBACK,
    COMPLIANCE_PATTERNS,
    QUESTIONS,
)
from voice_shopping_api.agents.state import (
    EmotionalResponseResult,
    ProductReason,
    ReasonPublisher,
    ShoppingRuntimeDependencies,
    ShoppingState,
)
from voice_shopping_api.core.product_embedding import ATTRIBUTE_KEY_LABELS, render_attribute_value
from voice_shopping_api.core.text import split_sentences

logger = logging.getLogger(__name__)
REASON_MODEL_CONCURRENCY = 3
INSUFFICIENT_COMPARISON_NOTE = "当前资料不足以按不同偏好进一步区分"
# Numeric attributes with an unambiguous "higher is better" interpretation.
# They are handled as a comparison across cards instead of as independent
# unique values, otherwise a lower value can be presented as a preference.
_NUMERIC_PREFERENCE_ATTRIBUTES = {
    "batteryHours": "续航",
}
_NUMERIC_VALUE_PATTERN = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)")
_SELECTION_CLAUSE_PATTERN = re.compile(
    r"如果您(?P<condition>[^，,；。！？!?]+)[，,]\s*推荐您选择第(?P<index>\d+)款\s*"
    r"[（(](?P<name>[^）)]+)[）)]"
)


def is_compliant(text_value: str) -> bool:
    return not any(pattern.search(text_value) for pattern in COMPLIANCE_PATTERNS)


def _fallback_reason(index: int, card: dict[str, Any]) -> ProductReason:
    point = (card.get("sellingPoints") or ["整体匹配你的需求"])[0]
    reason = f"{_card_label(index, card)}：{point}，且价格符合当前筛选条件。"
    if not is_compliant(reason):
        reason = f"{_card_label(index, card)}符合你当前的筛选条件。"
    return ProductReason(product_id=card["productId"], reason=reason)


def _card_name(card: dict[str, Any]) -> str:
    return str(card.get("name") or "该商品").strip() or "该商品"


def _card_label(index: int, card: dict[str, Any]) -> str:
    return f"第{index}款（{_card_name(card)}）"


_GENERIC_PRODUCT_PREFIXES = (
    "这款商品",
    "这款产品",
    "这款",
    "该款商品",
    "该款产品",
    "该款",
    "这件商品",
    "这件产品",
    "该商品",
    "该产品",
)


def _ensure_reason_identity(
    index: int, card: dict[str, Any], reason: ProductReason
) -> ProductReason:
    """Make the displayed product identity explicit in every spoken reason."""
    label = _card_label(index, card)
    text = reason.reason.strip()
    if text.startswith(label):
        normalized = text
    else:
        name = _card_name(card)
        body = text
        if body.startswith(name):
            body = body[len(name) :].lstrip(" ：:，,")
            normalized = f"{label}：{body}" if body else label
        else:
            for prefix in _GENERIC_PRODUCT_PREFIXES:
                if body.startswith(prefix):
                    body = body[len(prefix) :].lstrip(" ：:，,")
                    normalized = f"{label}：{body}" if body else label
                    break
            else:
                normalized = f"{label}：{body}" if body else label
    if not is_compliant(normalized):
        raise ValueError("带商品身份的推荐理由未通过合规检查")
    return ProductReason(product_id=card["productId"], reason=normalized)


def _comparison_key(value: object) -> str:
    return re.sub(r"[\W_]+", "", str(value)).casefold()


def _condition_key(value: object) -> str:
    key = _comparison_key(value)
    for prefix in ("更看重", "更在意", "看重", "在意", "需要", "希望", "偏好", "追求", "想要"):
        if key.startswith(prefix):
            return key[len(prefix) :]
    return key


def _has_comparison_value(value: object) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _numeric_value(value: object) -> float | None:
    """Parse a numeric attribute while rejecting booleans and non-finite values."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
    elif isinstance(value, str):
        match = _NUMERIC_VALUE_PATTERN.search(value.replace(",", ""))
        if match is None:
            return None
        parsed = float(match.group(0))
    else:
        return None
    return parsed if math.isfinite(parsed) else None


def _numeric_attribute_leader(
    cards: list[dict[str, Any]], key: str
) -> tuple[int, float, str] | None:
    """Return the unique card with the largest complete numeric attribute."""
    values: list[float] = []
    for card in cards:
        attributes = card.get("attributes")
        if not isinstance(attributes, dict) or key not in attributes:
            return None
        parsed = _numeric_value(attributes[key])
        if parsed is None:
            return None
        values.append(parsed)
    if len(values) < 2 or len(set(values)) < 2:
        return None
    leader_value = max(values)
    if values.count(leader_value) != 1:
        return None
    leader_index = values.index(leader_value)
    return leader_index, leader_value, render_attribute_value(key, leader_value)


def _unique_attribute_highlight(cards: list[dict[str, Any]], index: int) -> str | None:
    attributes = cards[index].get("attributes")
    if not isinstance(attributes, dict):
        return None
    all_attributes = [
        card.get("attributes") if isinstance(card.get("attributes"), dict) else {}
        for card in cards
    ]
    for key in sorted(attributes):
        # Numeric preference fields are compared in the directionally-aware
        # path below; never describe a lower value as an independent highlight.
        if key in _NUMERIC_PREFERENCE_ATTRIBUTES:
            continue
        value = attributes[key]
        if not _has_comparison_value(value):
            continue
        value_key = _comparison_key(value)
        matches = sum(
            1
            for candidate in all_attributes
            if key in candidate
            and _has_comparison_value(candidate[key])
            and _comparison_key(candidate[key]) == value_key
        )
        if matches != 1:
            continue
        label = ATTRIBUTE_KEY_LABELS.get(key, key)
        rendered = render_attribute_value(key, value)
        if rendered:
            if isinstance(value, bool):
                return f"具备{label}" if value else f"不具备{label}"
            return f"{label}为{rendered}"
    return None


def _unique_selling_point_highlight(cards: list[dict[str, Any]], index: int) -> str | None:
    card = cards[index]
    selling_points = card.get("sellingPoints")
    if not isinstance(selling_points, list):
        return None
    card_name_key = _comparison_key(_card_name(card))
    for point in selling_points:
        text = str(point).strip()
        point_key = _comparison_key(text)
        if not text or (card_name_key and card_name_key in point_key):
            continue
        matches = 0
        for candidate in cards:
            candidate_points = candidate.get("sellingPoints")
            if not isinstance(candidate_points, list):
                continue
            if any(
                _comparison_key(candidate_point) == point_key
                for candidate_point in candidate_points
            ):
                matches += 1
        if matches == 1:
            return text
    return None


def _price_leader_index(cards: list[dict[str, Any]]) -> int | None:
    prices: list[float] = []
    for card in cards:
        try:
            price = float(card["price"])
        except (KeyError, TypeError, ValueError):
            return None
        prices.append(price)
    lowest = min(prices)
    if len(set(prices)) < 2 or prices.count(lowest) != 1:
        return None
    return prices.index(lowest)


def _selection_options(cards: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """Return only fact-backed conditions that identify one displayed product."""
    if not cards:
        return [], ""
    if len(cards) == 1:
        highlight = _unique_attribute_highlight(cards, 0) or _unique_selling_point_highlight(
            cards, 0
        )
        highlight = highlight or "当前筛选条件"
        return [
            {
                "displayNumber": 1,
                "productId": str(cards[0].get("productId") or ""),
                "name": _card_name(cards[0]),
                "condition": f"更看重{highlight}",
            }
        ], ""

    options: list[dict[str, Any]] = []
    selected_indexes: set[int] = set()
    used_condition_keys: set[str] = set()
    has_complete_numeric_comparison = False
    price_leader = _price_leader_index(cards)
    if price_leader is not None:
        options.append(
            {
                "displayNumber": price_leader + 1,
                "productId": str(cards[price_leader].get("productId") or ""),
                "name": _card_name(cards[price_leader]),
                "condition": "更在意性价比",
            }
        )
        selected_indexes.add(price_leader)
        used_condition_keys.add(_condition_key("性价比"))

    # For numeric preferences, only the unique maximum is a valid leader.
    # The complete comparison covers all cards, even when the same card also
    # wins on price, so the hook can safely contain two conditions for it.
    for key, label in _NUMERIC_PREFERENCE_ATTRIBUTES.items():
        leader = _numeric_attribute_leader(cards, key)
        if leader is None:
            continue
        leader_index, _, rendered = leader
        condition = f"更在意{label}"
        condition_key = _condition_key(condition)
        if condition_key in used_condition_keys:
            continue
        options.append(
            {
                "displayNumber": leader_index + 1,
                "productId": str(cards[leader_index].get("productId") or ""),
                "name": _card_name(cards[leader_index]),
                "condition": condition,
                "fact": f"它的{label}可达{rendered}",
            }
        )
        selected_indexes.add(leader_index)
        used_condition_keys.add(condition_key)
        has_complete_numeric_comparison = True

    for index, card in enumerate(cards):
        if index in selected_indexes:
            continue
        highlight = _unique_attribute_highlight(cards, index) or _unique_selling_point_highlight(
            cards, index
        )
        if not highlight:
            continue
        condition = f"更看重{highlight}"
        condition_key = _condition_key(condition)
        if condition_key in used_condition_keys:
            continue
        options.append(
            {
                "displayNumber": index + 1,
                "productId": str(card.get("productId") or ""),
                "name": _card_name(card),
                "condition": condition,
            }
        )
        selected_indexes.add(index)
        used_condition_keys.add(condition_key)

    if len(selected_indexes) == len(cards) or has_complete_numeric_comparison:
        return options, ""
    if not options:
        return options, f"这些商品的{INSUFFICIENT_COMPARISON_NOTE}"
    return options, f"其余商品的{INSUFFICIENT_COMPARISON_NOTE}"


def _fallback_recommendation_hook(cards: list[dict[str, Any]]) -> str:
    """Build a useful, fact-only choice prompt when the hook model is unavailable."""
    options, insufficiency_note = _selection_options(cards)
    if not options and not insufficiency_note:
        return ""
    clauses = [
        f"如果您{option['condition']}，推荐您选择"
        f"{_card_label(int(option['displayNumber']), cards[int(option['displayNumber']) - 1])}"
        + (f"，{option['fact']}" if option.get("fact") else "")
        for option in options
    ]
    if insufficiency_note:
        clauses.append(insufficiency_note)
    return "；".join(clauses) + "。"


def _is_usable_hook(
    hook: str,
    cards: list[dict[str, Any]],
    selection_options: list[dict[str, Any]] | None = None,
    insufficiency_note: str | None = None,
) -> bool:
    if not hook or not is_compliant(hook):
        return False
    if selection_options is None or insufficiency_note is None:
        selection_options, insufficiency_note = _selection_options(cards)
    matches = list(_SELECTION_CLAUSE_PATTERN.finditer(hook))
    references = [
        (
            int(match.group("index")),
            match.group("name").strip(),
            _condition_key(match.group("condition")),
        )
        for match in matches
    ]
    referenced_indexes = {index for index, _, _ in references}
    raw_referenced_indexes = {int(index) for index in re.findall(r"第(\d+)款", hook)}
    expected_options: list[tuple[int, str, str, str]] = []
    for option in selection_options:
        try:
            index = int(option["displayNumber"])
        except (KeyError, TypeError, ValueError):
            return False
        if index < 1 or index > len(cards):
            return False
        expected_options.append(
            (
                index,
                _card_name(cards[index - 1]),
                _condition_key(option["condition"]),
                str(option.get("fact") or ""),
            )
        )
    expected_conditions = {condition for _, _, condition, _ in expected_options}
    if len(expected_conditions) != len(expected_options):
        return False
    actual_pairs = {(index, condition) for index, _, condition in references}
    expected_pairs = {(index, condition) for index, _, condition, _ in expected_options}
    if len(references) != len(actual_pairs):
        return False
    if raw_referenced_indexes != referenced_indexes:
        return False
    if len(references) != len(expected_options) or actual_pairs != expected_pairs:
        return False
    if "推荐您选择" in hook and not references:
        return False
    for index, name, condition in references:
        expected_names = {
            expected_name
            for expected_index, expected_name, expected_condition, _ in expected_options
            if expected_index == index and expected_condition == condition
        }
        if name not in expected_names:
            return False
    for _, _, _, fact in expected_options:
        if fact and fact not in hook:
            return False
    if insufficiency_note:
        return insufficiency_note in hook
    return INSUFFICIENT_COMPARISON_NOTE not in hook


def _complete_sentence(text_value: str) -> str:
    text_value = text_value.strip()
    if text_value.endswith(("。", "！", "？", "!", "?")):
        return text_value
    return f"{text_value}。"


async def _generate_recommendation_hook(state: ShoppingState) -> str:
    cards = state["product_cards"]
    selection_options, insufficiency_note = _selection_options(cards)
    fallback = _fallback_recommendation_hook(cards)
    if not selection_options and not insufficiency_note:
        return ""
    if not state.get("model_enabled"):
        return fallback
    try:
        hook = await generate_recommendation_hook(
            state.get("utterance", ""),
            cards,
            state.get("emotion_style", "warm-professional"),
            selection_options,
            insufficiency_note,
        )
        if not _is_usable_hook(hook, cards, selection_options, insufficiency_note):
            raise ValueError("模型返回的选择钩子不完整、不合规或未按已验证差异引用商品")
        return _complete_sentence(hook)
    except Exception as exc:
        logger.warning("Recommendation hook model failed; using deterministic fallback: %s", exc)
        return fallback


async def _generate_one_reason(
    index: int,
    card: dict[str, Any],
    utterance: str,
    emotion_style: str,
    reason_publisher: ReasonPublisher | None,
    semaphore: asyncio.Semaphore,
) -> ProductReason:
    async with semaphore:
        try:
            reason = await generate_product_reason(utterance, card, emotion_style)
            if not is_compliant(reason.reason):
                raise ValueError("模型返回的推荐理由未通过合规检查")
            reason = _ensure_reason_identity(index, card, reason)
        except Exception as exc:
            logger.warning(
                "Product reason model failed for %s; using deterministic fallback: %s",
                card.get("productId"),
                exc,
            )
            reason = _fallback_reason(index, card)
    if reason_publisher:
        await reason_publisher(reason)
    return reason


async def _generate_reasons(
    state: ShoppingState,
    reason_publisher: ReasonPublisher | None,
) -> list[ProductReason]:
    cards = state["product_cards"]
    if not state.get("model_enabled"):
        reasons = [_fallback_reason(index, card) for index, card in enumerate(cards, start=1)]
        if reason_publisher:
            for reason in reasons:
                await reason_publisher(reason)
        return reasons
    semaphore = asyncio.Semaphore(min(REASON_MODEL_CONCURRENCY, len(cards)))
    return list(
        await asyncio.gather(
            *(
                _generate_one_reason(
                    index,
                    card,
                    state.get("utterance", ""),
                    state.get("emotion_style", "warm-professional"),
                    reason_publisher,
                    semaphore,
                )
                for index, card in enumerate(cards, start=1)
            )
        )
    )


def _build_speech(reasons: list[ProductReason], hook: str = "") -> str:
    speech = "我筛选出了三款商品。" if len(reasons) == 3 else f"我找到了{len(reasons)}款商品。"
    speech += " ".join(reason.reason for reason in reasons)
    return f"{speech} {hook}" if hook else speech


async def _publish_speech(
    speech: str,
    context: ShoppingRuntimeDependencies | None,
) -> tuple[bool, bool]:
    if context is None:
        return False, False
    speech_streamed = False
    if context.speech_delta_publisher:
        for start in range(0, len(speech), 12):
            delta = speech[start : start + 12]
            if delta:
                await context.speech_delta_publisher(delta)
        speech_streamed = bool(speech)
    speech_audio_streamed = False
    if context.speech_sentence_publisher:
        for sentence in split_sentences(speech):
            await context.speech_sentence_publisher(sentence)
        speech_audio_streamed = True
    return speech_streamed, speech_audio_streamed


async def order_response(
    state: ShoppingState, runtime: Runtime[ShoppingRuntimeDependencies]
) -> dict[str, Any]:
    context = runtime.context
    if context is None or context.order_handler is None:
        reply = "正在处理你的订单请求。"
        return {"speech_text": reply, "final_reply": reply}
    return await context.order_handler(state)


async def emotional_response(
    state: ShoppingState, runtime: Runtime[ShoppingRuntimeDependencies]
) -> dict[str, Any]:
    if state.get("clarification_status") == "ASK":
        speech = (state.get("pending_question") or {}).get("question", QUESTIONS["productCategory"])
        result = EmotionalResponseResult(reasons=[], speech_text=speech)
    elif state.get("product_cards"):
        context = runtime.context
        reason_publisher = context.reason_publisher if context else None
        reasons, hook = await asyncio.gather(
            _generate_reasons(state, reason_publisher),
            _generate_recommendation_hook(state),
        )
        speech = _build_speech(reasons, hook)
        result = EmotionalResponseResult(reasons=reasons, speech_text=speech)
        return {
            "reasons": [reason.model_dump() for reason in result.reasons],
            "speech_text": result.speech_text,
            "final_reply": result.speech_text,
            "reasons_streamed": bool(reason_publisher),
        }
    else:
        intent = (state.get("intent") or {}).get("type")
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


async def publish_response(
    state: ShoppingState, runtime: Runtime[ShoppingRuntimeDependencies]
) -> dict[str, Any]:
    """Publish only the response that has passed the compliance branch."""
    context = runtime.context
    speech = state.get("speech_text") or state.get("final_reply", "")
    speech_streamed, speech_audio_streamed = await _publish_speech(speech, context)
    return {
        "final_reply": speech,
        "speech_streamed": speech_streamed,
        "speech_audio_streamed": speech_audio_streamed,
    }


async def violation_response(state: ShoppingState) -> dict[str, Any]:
    """Replace a response containing a forbidden expression with a safe reply."""
    return {
        "reasons": [],
        "speech_text": COMPLIANCE_FALLBACK,
        "final_reply": COMPLIANCE_FALLBACK,
        "compliance_blocked": True,
        "speech_streamed": False,
        "speech_audio_streamed": False,
    }


async def compliance_check(state: ShoppingState) -> dict[str, Any]:
    speech = state.get("speech_text", "")
    for sentence in split_sentences(speech):
        if not is_compliant(sentence):
            return {
                "compliance_blocked": True,
                "violation_sentence": sentence,
            }
    return {
        "compliance_blocked": False,
        "violation_sentence": None,
        "final_reply": speech,
    }
