import asyncio
import logging
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
from voice_shopping_api.core.text import split_sentences

logger = logging.getLogger(__name__)
REASON_MODEL_CONCURRENCY = 3


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


def _card_highlight(card: dict[str, Any]) -> str:
    selling_points = card.get("sellingPoints")
    if isinstance(selling_points, list):
        for point in selling_points:
            text = str(point).strip()
            if text:
                return text
    return "当前筛选条件"


def _price_leader_index(cards: list[dict[str, Any]]) -> int | None:
    prices: list[float] = []
    for card in cards:
        try:
            price = float(card["price"])
        except (KeyError, TypeError, ValueError):
            return None
        prices.append(price)
    if len(set(prices)) < 2:
        return None
    return prices.index(min(prices))


def _fallback_recommendation_hook(cards: list[dict[str, Any]]) -> str:
    """Build a useful, fact-only choice prompt when the hook model is unavailable."""
    if not cards:
        return ""
    price_leader = _price_leader_index(cards)
    clauses: list[str] = []
    for index, card in enumerate(cards):
        if index == price_leader:
            clauses.append(f"如果您更在意性价比，推荐您选择{_card_label(index + 1, card)}")
        else:
            clauses.append(
                f"如果您更看重{_card_highlight(card)}，推荐您选择{_card_label(index + 1, card)}"
            )
    return "；".join(clauses) + "。"


def _is_usable_hook(hook: str, cards: list[dict[str, Any]]) -> bool:
    if not hook or not is_compliant(hook):
        return False
    references = [
        (int(index), name.strip())
        for index, name in re.findall(r"第(\d+)款\s*[（(]([^）)]+)[）)]", hook)
    ]
    referenced_indexes = {index for index, _ in references}
    raw_referenced_indexes = {int(index) for index in re.findall(r"第(\d+)款", hook)}
    required_references = 2 if len(cards) > 1 else 1
    if len(referenced_indexes) < required_references:
        return False
    if raw_referenced_indexes != referenced_indexes:
        return False
    if any(index < 1 or index > len(cards) for index in referenced_indexes):
        return False
    return all(name == _card_name(cards[index - 1]) for index, name in references)


def _complete_sentence(text_value: str) -> str:
    text_value = text_value.strip()
    if text_value.endswith(("。", "！", "？", "!", "?")):
        return text_value
    return f"{text_value}。"


async def _generate_recommendation_hook(state: ShoppingState) -> str:
    cards = state["product_cards"]
    fallback = _fallback_recommendation_hook(cards)
    if not state.get("model_enabled"):
        return fallback
    try:
        hook = await generate_recommendation_hook(
            state.get("utterance", ""),
            cards,
            state.get("emotion_style", "warm-professional"),
        )
        if not _is_usable_hook(hook, cards):
            raise ValueError("模型返回的选择钩子不完整、不合规或未引用商品名称")
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
