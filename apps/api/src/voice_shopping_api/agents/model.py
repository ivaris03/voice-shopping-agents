import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_qwq import ChatQwen
from langsmith import get_current_run_tree, traceable

from voice_shopping_api.agents.prompts import (
    EMOTIONAL_RESPONSE_SYSTEM_PROMPT,
    PRODUCT_REASON_BATCH_SYSTEM_PROMPT,
    RECOMMENDATION_HOOK_SYSTEM_PROMPT,
    SLOT_EXTRACTION_SYSTEM_PROMPT,
    build_intent_system_prompt,
)
from voice_shopping_api.agents.state import (
    AgentModel,
    EmotionalResponseResult,
    IntentResult,
    ProductReason,
    ProductReasonBatch,
    RecommendationHook,
    SlotExtractionResult,
)
from voice_shopping_api.core.config import get_settings
from voice_shopping_api.core.embeddings import embed_text
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


def _chat_model(*, json_mode: bool = False) -> ChatQwen:
    settings = get_settings()
    if not settings.dashscope_api_key:
        raise RuntimeError("DashScope API key is not configured")
    kwargs: dict[str, Any] = {
        "model": settings.agent_model,
        "api_key": settings.dashscope_api_key,
        "base_url": settings.dashscope_chat_base_url,
        "enable_thinking": False,
        "temperature": 0.1,
        "timeout": 30,
        "max_retries": 2,
    }
    if json_mode:
        kwargs["model_kwargs"] = {"response_format": {"type": "json_object"}}
    return ChatQwen(
        **kwargs,
    )


@traceable(name="dashscope-chat", run_type="llm", tags=["dashscope", "chat"])
async def _chat_json(system_prompt: str, payload: dict[str, Any]) -> Any:
    settings = get_settings()
    message = await _chat_model(json_mode=True).ainvoke(
        [
            ("system", system_prompt),
            ("human", json.dumps(payload, ensure_ascii=False)),
        ]
    )
    _mark_model_run(settings.agent_model, _message_usage(message))
    return _parse_json(_message_content(message))


@traceable(
    name="dashscope-chat-structured",
    run_type="llm",
    tags=["dashscope", "chat", "structured"],
)
async def _structured_chat(
    system_prompt: str,
    payload: dict[str, Any],
    schema: type[AgentModel],
) -> AgentModel:
    """Invoke Qwen with a Pydantic-constrained output and retain usage data."""
    settings = get_settings()
    result = (
        await _chat_model()
        .with_structured_output(
            schema,
            include_raw=True,
        )
        .ainvoke(
            [
                ("system", system_prompt),
                ("human", json.dumps(payload, ensure_ascii=False)),
            ]
        )
    )
    if isinstance(result, schema):
        return result
    if not isinstance(result, dict):
        raise TypeError("Structured model response must be a parsed Pydantic object")
    raw = result.get("raw")
    _mark_model_run(settings.agent_model, _message_usage(raw))
    parsing_error = result.get("parsing_error")
    if parsing_error:
        raise parsing_error
    parsed = result.get("parsed")
    if isinstance(parsed, schema):
        return parsed
    return schema.model_validate(parsed)


async def embed_query(query: str) -> list[float]:
    settings = get_settings()
    vector, usage = await embed_text(query)
    _mark_model_run(settings.embedding_model, usage)
    return vector


async def recognize_with_model(
    utterance: str,
    conversation_history: list[str],
    taxonomy_categories: list[dict[str, Any]],
) -> IntentResult:
    return await _structured_chat(
        build_intent_system_prompt(taxonomy_categories),
        {"utterance": utterance, "recentConversation": conversation_history[-6:]},
        IntentResult,
    )


async def extract_slots_with_model(
    utterance: str,
    product_category: str,
    required_slots: list[str],
    current_slots: dict[str, Any],
    pending_question: dict[str, Any] | None,
    slot_definitions: dict[str, dict[str, Any]],
    conversation_history: list[str],
) -> dict[str, Any]:
    result = await _structured_chat(
        SLOT_EXTRACTION_SYSTEM_PROMPT,
        {
            "utterance": utterance,
            "productCategory": product_category,
            "requiredSlots": required_slots,
            "currentSlots": current_slots,
            "pendingQuestion": pending_question,
            "slotDefinitions": slot_definitions,
            "recentConversation": conversation_history[-6:],
        },
        SlotExtractionResult,
    )
    return result.slots


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

    async for chunk in _chat_model(json_mode=True).astream(
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


async def generate_product_reasons(
    utterance: str,
    product_cards: list[dict[str, Any]],
    emotion_style: str,
) -> list[ProductReason]:
    """Generate all Top-3 reasons in one structured model invocation."""
    batch = await _structured_chat(
        PRODUCT_REASON_BATCH_SYSTEM_PROMPT,
        {
            "utterance": utterance,
            "emotionStyle": emotion_style,
            "productCards": product_cards,
        },
        ProductReasonBatch,
    )
    expected_ids = [str(card["productId"]) for card in product_cards]
    actual_ids = [reason.product_id for reason in batch.reasons]
    if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != set(expected_ids):
        raise ValueError("模型批量返回的商品 ID 与输入商品事实不一致")
    by_id = {reason.product_id: reason for reason in batch.reasons}
    ordered = [by_id[product_id] for product_id in expected_ids]
    if any(not reason.reason.strip() for reason in ordered):
        raise ValueError("模型批量返回的推荐理由为空")
    return ordered


async def generate_recommendation_hook(
    utterance: str,
    product_cards: list[dict[str, Any]],
    emotion_style: str,
    selection_options: list[dict[str, Any]] | None = None,
) -> str:
    """Generate one selection hook from server-validated comparison options."""
    result = await _structured_chat(
        RECOMMENDATION_HOOK_SYSTEM_PROMPT,
        {
            "utterance": utterance,
            "emotionStyle": emotion_style,
            "productCards": product_cards,
            "selectionOptions": selection_options or [],
        },
        RecommendationHook,
    )
    hook = result.hook.strip()
    if not hook:
        raise ValueError("模型返回的选择钩子为空")
    return hook
