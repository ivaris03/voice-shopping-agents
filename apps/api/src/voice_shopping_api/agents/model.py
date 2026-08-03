import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any

import dashscope
from langchain_community.document_compressors import DashScopeRerank
from langchain_qwq import ChatQwen
from langsmith import get_current_run_tree, traceable

from voice_shopping_api.agents.prompts import (
    CLARIFICATION_SYSTEM_PROMPT,
    EMOTIONAL_RESPONSE_SYSTEM_PROMPT,
    PRODUCT_REASON_SYSTEM_PROMPT,
    RECOMMENDATION_RERANK_INSTRUCTION,
    build_intent_system_prompt,
)
from voice_shopping_api.agents.state import (
    EmotionalResponseResult,
    IntentResult,
    ProductReason,
    SlotExtractionResult,
)
from voice_shopping_api.core.config import get_settings
from voice_shopping_api.core.embeddings import embed_text
from voice_shopping_api.core.observability import (
    finish_trace,
    response_request_id,
    response_usage,
    start_trace,
)
from voice_shopping_api.core.text import take_completed_sentences


def _parse_json(content: str) -> Any:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    return json.loads(cleaned)


def _mark_model_run(model: str, usage: dict[str, Any] | None = None) -> None:
    run = get_current_run_tree()
    if run is None:
        return
    run.add_metadata({"ls_provider": "dashscope", "ls_model_name": model})
    if not usage:
        return
    input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)
    run.set(
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }
    )


def _message_content(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = [part.get("text", "") for part in content if isinstance(part, dict)]
        return "".join(str(part) for part in text_parts)
    return str(content)


def _message_usage(message: Any) -> dict[str, Any] | None:
    usage = getattr(message, "usage_metadata", None)
    if isinstance(usage, dict):
        return usage
    response_metadata = getattr(message, "response_metadata", None)
    if not isinstance(response_metadata, dict):
        return None
    token_usage = response_metadata.get("token_usage") or response_metadata.get("usage")
    return token_usage if isinstance(token_usage, dict) else None


class _InstructionalRerankClient:
    """Pass the existing rerank instruction through LangChain's client hook."""

    def __init__(self, instruction: str) -> None:
        self.instruction = instruction
        self.response: Any = None

    def call(self, **kwargs: Any) -> Any:
        self.response = dashscope.TextReRank.call(instruct=self.instruction, **kwargs)
        return self.response


def _chat_model() -> ChatQwen:
    settings = get_settings()
    if not settings.dashscope_api_key:
        raise RuntimeError("DashScope API key is not configured")
    return ChatQwen(
        model=settings.agent_model,
        api_key=settings.dashscope_api_key,
        base_url=settings.dashscope_chat_base_url,
        model_kwargs={"response_format": {"type": "json_object"}},
        enable_thinking=False,
        temperature=0.1,
        timeout=30,
        max_retries=2,
    )


@traceable(name="dashscope-chat", run_type="llm", tags=["dashscope", "chat"])
async def _chat_json(system_prompt: str, payload: dict[str, Any]) -> Any:
    settings = get_settings()
    message = await _chat_model().ainvoke(
        [
            ("system", system_prompt),
            ("human", json.dumps(payload, ensure_ascii=False)),
        ]
    )
    _mark_model_run(settings.agent_model, _message_usage(message))
    return _parse_json(_message_content(message))


async def embed_query(query: str) -> list[float]:
    settings = get_settings()
    vector, usage = await embed_text(query)
    _mark_model_run(settings.embedding_model, usage)
    return vector


async def rerank_products(query: str, products: list[dict[str, Any]]) -> dict[str, float]:
    settings = get_settings()
    documents = [
        json.dumps(
            {
                "name": product.get("name"),
                "brand": product.get("brand"),
                "description": product.get("description"),
                "sellingPoints": product.get("selling_points", []),
                "attributes": product.get("attributes", {}),
            },
            ensure_ascii=False,
        )
        for product in products
    ]
    started = perf_counter()
    span = start_trace(
        "dashscope-rerank",
        run_type="retriever",
        inputs={"query": query, "documents": documents},
        metadata={
            "ls_provider": "dashscope",
            "ls_model_name": settings.reranker_model,
            "operation": "rerank",
            "document_count": len(documents),
            "instruction": RECOMMENDATION_RERANK_INSTRUCTION,
            "query": query,
            "documents": documents,
        },
        tags=["dashscope", "rerank"],
        project_name=settings.langsmith_project,
    )
    recording_client = _InstructionalRerankClient(RECOMMENDATION_RERANK_INSTRUCTION)

    def call() -> Any:
        dashscope.api_key = settings.dashscope_api_key
        dashscope.base_http_api_url = settings.dashscope_http_base_url
        reranker = DashScopeRerank(
            client=recording_client,
            model=settings.reranker_model,
            top_n=len(documents),
            dashscope_api_key=settings.dashscope_api_key,
        )
        return reranker.rerank(documents, query, top_n=len(documents))

    try:
        response = await asyncio.to_thread(call)
        usage = response_usage(recording_client.response)
        _mark_model_run(settings.reranker_model, usage)
        scores: dict[str, float] = {}
        for item in response:
            product = products[int(item["index"])]
            scores[str(product["id"])] = float(item["relevance_score"])
        finish_trace(
            span,
            outputs={"scores": scores, "result_count": len(scores)},
            metadata={
                "status": "ok",
                "duration_ms": round((perf_counter() - started) * 1000, 2),
                "query": query,
                "documents": documents,
                "result_count": len(scores),
                "request_id": response_request_id(recording_client.response),
                "scores": scores,
            },
            usage=usage,
        )
        return scores
    except Exception as exc:
        finish_trace(
            span,
            metadata={
                "status": "error",
                "duration_ms": round((perf_counter() - started) * 1000, 2),
            },
            error=exc,
        )
        raise


async def recognize_with_model(
    utterance: str,
    conversation_history: list[str],
    taxonomy_categories: list[dict[str, Any]],
) -> IntentResult:
    result = await _chat_json(
        build_intent_system_prompt(taxonomy_categories),
        {"utterance": utterance, "recentConversation": conversation_history[-6:]},
    )
    return IntentResult.model_validate(result["intent"])


async def clarify_with_model(
    utterance: str,
    product_category: str,
    required_slots: list[str],
    current_slots: dict[str, Any],
    pending_question: dict[str, Any] | None,
    slot_definitions: dict[str, dict[str, Any]],
    conversation_history: list[str],
) -> dict[str, Any]:
    result = await _chat_json(
        CLARIFICATION_SYSTEM_PROMPT,
        {
            "utterance": utterance,
            "productCategory": product_category,
            "requiredSlots": required_slots,
            "currentSlots": current_slots,
            "pendingQuestion": pending_question,
            "slotDefinitions": slot_definitions,
            "recentConversation": conversation_history[-6:],
        },
    )
    return SlotExtractionResult.model_validate(result).slots


async def _stream_chat_json(
    system_prompt: str,
    payload: dict[str, Any],
    on_speech_delta: Callable[[str], Awaitable[None]] | None = None,
    on_speech_sentence: Callable[[str], Awaitable[None]] | None = None,
) -> Any:
    """Stream ``speech_text`` and publish each completed short sentence."""
    settings = get_settings()

    content_parts: list[str] = []
    speech_started = False
    speech_ended = False
    speech_offset = 0
    speech_buffer = ""
    sentence_buffer = ""
    usage: dict[str, Any] | None = None

    async def publish_available_speech() -> None:
        nonlocal speech_started, speech_ended, speech_offset, speech_buffer, sentence_buffer
        content = "".join(content_parts)
        if not speech_started:
            match = re.search(r'"speech_text"\s*:\s*"', content)
            if match is None:
                return
            speech_started = True
            speech_offset = match.end()
        while speech_offset < len(content) and not speech_ended:
            character = content[speech_offset]
            if character == '"':
                speech_ended = True
                speech_offset += 1
                break
            if character != "\\":
                speech_buffer += character
                speech_offset += 1
                continue
            if speech_offset + 1 >= len(content):
                break
            escaped = content[speech_offset + 1]
            simple_escapes = {
                '"': '"',
                "\\": "\\",
                "/": "/",
                "b": "\b",
                "f": "\f",
                "n": "\n",
                "r": "\r",
                "t": "\t",
            }
            if escaped == "u":
                if speech_offset + 6 > len(content):
                    break
                hex_value = content[speech_offset + 2 : speech_offset + 6]
                if not re.fullmatch(r"[0-9a-fA-F]{4}", hex_value):
                    raise ValueError("Invalid unicode escape in streamed model response")
                speech_buffer += chr(int(hex_value, 16))
                speech_offset += 6
            elif escaped in simple_escapes:
                speech_buffer += simple_escapes[escaped]
                speech_offset += 2
            else:
                raise ValueError("Invalid escape in streamed model response")
        if speech_buffer:
            delta = speech_buffer
            speech_buffer = ""
            if on_speech_delta:
                await on_speech_delta(delta)
            if on_speech_sentence:
                sentence_buffer += delta
                sentences, sentence_buffer = take_completed_sentences(sentence_buffer)
                for sentence in sentences:
                    await on_speech_sentence(sentence)

    async for chunk in _chat_model().astream(
        [
            ("system", system_prompt),
            ("human", json.dumps(payload, ensure_ascii=False)),
        ]
    ):
        usage = _message_usage(chunk) or usage
        chunk_content = _message_content(chunk)
        if not chunk_content:
            continue
        content_parts.append(chunk_content)
        await publish_available_speech()
    if not speech_ended:
        raise ValueError("Streamed model response did not contain a complete speech_text field")
    if on_speech_sentence and sentence_buffer.strip():
        await on_speech_sentence(sentence_buffer.strip())
    _mark_model_run(settings.agent_model, usage)
    return _parse_json("".join(content_parts))


async def respond_with_model(
    utterance: str,
    product_cards: list[dict[str, Any]],
    emotion_style: str,
    on_speech_delta: Callable[[str], Awaitable[None]] | None = None,
    on_speech_sentence: Callable[[str], Awaitable[None]] | None = None,
) -> EmotionalResponseResult:
    payload = {"utterance": utterance, "emotionStyle": emotion_style, "productCards": product_cards}
    if on_speech_sentence:
        result = await _stream_chat_json(
            EMOTIONAL_RESPONSE_SYSTEM_PROMPT,
            payload,
            on_speech_delta,
            on_speech_sentence,
        )
    elif on_speech_delta:
        result = await _stream_chat_json(EMOTIONAL_RESPONSE_SYSTEM_PROMPT, payload, on_speech_delta)
    else:
        result = await _chat_json(EMOTIONAL_RESPONSE_SYSTEM_PROMPT, payload)
    response = EmotionalResponseResult.model_validate(result)
    expected_ids = {card["productId"] for card in product_cards}
    actual_ids = {reason.product_id for reason in response.reasons}
    if actual_ids != expected_ids:
        raise ValueError("模型返回的商品 ID 与事实不一致")
    return EmotionalResponseResult(
        reasons=[ProductReason.model_validate(reason) for reason in response.reasons],
        speech_text=response.speech_text,
    )


async def generate_product_reason(
    utterance: str,
    product_card: dict[str, Any],
    emotion_style: str,
) -> ProductReason:
    """Generate and validate one reason for one immutable product card."""
    product_id = str(product_card["productId"])
    result = await _chat_json(
        PRODUCT_REASON_SYSTEM_PROMPT,
        {
            "utterance": utterance,
            "emotionStyle": emotion_style,
            "productCard": product_card,
        },
    )
    reason = ProductReason.model_validate(result)
    if reason.product_id != product_id:
        raise ValueError("模型返回的商品 ID 与输入商品事实不一致")
    if not reason.reason.strip():
        raise ValueError("模型返回的推荐理由为空")
    return reason
