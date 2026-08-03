import asyncio
import logging
from typing import Any

from langgraph.runtime import Runtime

from voice_shopping_api.agents.model import generate_product_reason
from voice_shopping_api.agents.nodes.constants import (
    COMPLIANCE_FALLBACK,
    COMPLIANCE_PATTERNS,
    QUESTIONS,
)
from voice_shopping_api.agents.state import (
    EmotionalResponseResult,
    ProductReason,
    ReasonPublisher,
    ShoppingState,
    ShoppingWorkflowContext,
)
from voice_shopping_api.core.text import split_sentences

logger = logging.getLogger(__name__)
REASON_MODEL_CONCURRENCY = 3


def is_compliant(text_value: str) -> bool:
    return not any(pattern.search(text_value) for pattern in COMPLIANCE_PATTERNS)


def _fallback_reason(index: int, card: dict[str, Any]) -> ProductReason:
    point = (card.get("sellingPoints") or ["整体匹配你的需求"])[0]
    reason = f"第{index}款{card['name']}：{point}，且价格符合当前筛选条件。"
    if not is_compliant(reason):
        reason = f"第{index}款商品符合你当前的筛选条件。"
    return ProductReason(product_id=card["productId"], reason=reason)


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


def _build_speech(reasons: list[ProductReason]) -> str:
    speech = "我筛选出了三款商品。" if len(reasons) == 3 else f"我找到了{len(reasons)}款商品。"
    return speech + " ".join(reason.reason for reason in reasons)


async def _publish_speech(
    speech: str,
    context: ShoppingWorkflowContext | None,
) -> tuple[bool, bool]:
    if context is None:
        return False, False
    speech_streamed = False
    if context.speech_delta_publisher:
        for start in range(0, len(speech), 12):
            delta = speech[start : start + 12]
            if is_compliant(delta):
                await context.speech_delta_publisher(delta)
        speech_streamed = True
    speech_audio_streamed = False
    if context.speech_sentence_publisher:
        for sentence in split_sentences(speech):
            await context.speech_sentence_publisher(sentence)
        speech_audio_streamed = True
    return speech_streamed, speech_audio_streamed


async def order_response(
    state: ShoppingState, runtime: Runtime[ShoppingWorkflowContext]
) -> dict[str, Any]:
    context = runtime.context
    if context is None or context.order_handler is None:
        reply = "正在处理你的订单请求。"
        return {"speech_text": reply, "final_reply": reply}
    return await context.order_handler(state)


async def emotional_response(
    state: ShoppingState, runtime: Runtime[ShoppingWorkflowContext]
) -> dict[str, Any]:
    if state.get("clarification_status") == "ASK":
        speech = (state.get("pending_question") or {}).get("question", QUESTIONS["productCategory"])
        result = EmotionalResponseResult(reasons=[], speech_text=speech)
    elif state.get("product_cards"):
        context = runtime.context
        reason_publisher = context.reason_publisher if context else None
        reasons = await _generate_reasons(state, reason_publisher)
        speech = _build_speech(reasons)
        speech_streamed, speech_audio_streamed = await _publish_speech(speech, context)
        result = EmotionalResponseResult(reasons=reasons, speech_text=speech)
        return {
            "reasons": [reason.model_dump() for reason in result.reasons],
            "speech_text": result.speech_text,
            "final_reply": result.speech_text,
            "reasons_streamed": bool(reason_publisher),
            "speech_streamed": speech_streamed,
            "speech_audio_streamed": speech_audio_streamed,
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
