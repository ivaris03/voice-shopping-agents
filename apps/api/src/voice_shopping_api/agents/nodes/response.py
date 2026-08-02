import logging
from typing import Any

from langgraph.runtime import Runtime

from voice_shopping_api.agents.model import respond_with_model
from voice_shopping_api.agents.nodes.constants import (
    COMPLIANCE_FALLBACK,
    COMPLIANCE_PATTERNS,
    QUESTIONS,
)
from voice_shopping_api.agents.state import (
    EmotionalResponseResult,
    ProductReason,
    ShoppingState,
    ShoppingWorkflowContext,
)

logger = logging.getLogger(__name__)


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
        if state.get("model_enabled"):
            try:
                model_result = await respond_with_model(
                    state.get("utterance", ""),
                    state["product_cards"],
                    state.get("emotion_style", "warm-professional"),
                    runtime.context.speech_delta_publisher if runtime.context else None,
                    runtime.context.speech_sentence_publisher if runtime.context else None,
                )
                return {
                    "reasons": [reason.model_dump() for reason in model_result.reasons],
                    "speech_text": model_result.speech_text,
                    "final_reply": model_result.speech_text,
                    "speech_streamed": bool(
                        runtime.context and runtime.context.speech_delta_publisher
                    ),
                    "speech_audio_streamed": bool(
                        runtime.context and runtime.context.speech_sentence_publisher
                    ),
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
