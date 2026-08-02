import asyncio
import json
import re
from typing import Any

import dashscope
import httpx
from langsmith import get_current_run_tree, traceable

from voice_shopping_api.agents.prompts import (
    CLARIFICATION_SYSTEM_PROMPT,
    EMOTIONAL_RESPONSE_SYSTEM_PROMPT,
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


@traceable(name="dashscope-chat", run_type="llm", tags=["dashscope", "chat"])
async def _chat_json(system_prompt: str, payload: dict[str, Any]) -> Any:
    settings = get_settings()
    if not settings.dashscope_api_key:
        raise RuntimeError("DashScope API key is not configured")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{settings.dashscope_chat_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.dashscope_api_key}"},
            json={
                "model": settings.agent_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.1,
            },
        )
        response.raise_for_status()
    response_data = response.json()
    _mark_model_run(settings.agent_model, response_data.get("usage"))
    content = response_data["choices"][0]["message"]["content"]
    return _parse_json(content)


@traceable(name="dashscope-embedding", run_type="embedding", tags=["dashscope", "embedding"])
async def embed_query(query: str) -> list[float]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{settings.dashscope_chat_base_url.rstrip('/')}/embeddings",
            headers={"Authorization": f"Bearer {settings.dashscope_api_key}"},
            json={
                "model": settings.embedding_model,
                "input": query,
                "dimensions": 1024,
                "encoding_format": "float",
            },
        )
        response.raise_for_status()
    response_data = response.json()
    _mark_model_run(settings.embedding_model, response_data.get("usage"))
    return [float(value) for value in response_data["data"][0]["embedding"]]


@traceable(name="dashscope-rerank", run_type="retriever", tags=["dashscope", "rerank"])
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

    def call() -> Any:
        dashscope.api_key = settings.dashscope_api_key
        dashscope.base_http_api_url = settings.dashscope_http_base_url
        return dashscope.TextReRank.call(
            model=settings.reranker_model,
            query=query,
            documents=documents,
            top_n=len(documents),
            return_documents=False,
            instruct=RECOMMENDATION_RERANK_INSTRUCTION,
        )

    response = await asyncio.to_thread(call)
    if response.status_code != 200:
        raise RuntimeError(response.message)
    _mark_model_run(settings.reranker_model)
    scores: dict[str, float] = {}
    for item in response.output.get("results", []):
        product = products[int(item["index"])]
        scores[str(product["id"])] = float(item["relevance_score"])
    return scores


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


async def respond_with_model(
    utterance: str,
    product_cards: list[dict[str, Any]],
    emotion_style: str,
) -> EmotionalResponseResult:
    result = await _chat_json(
        EMOTIONAL_RESPONSE_SYSTEM_PROMPT,
        {"utterance": utterance, "emotionStyle": emotion_style, "productCards": product_cards},
    )
    response = EmotionalResponseResult.model_validate(result)
    expected_ids = {card["productId"] for card in product_cards}
    actual_ids = {reason.product_id for reason in response.reasons}
    if actual_ids != expected_ids:
        raise ValueError("模型返回的商品 ID 与事实不一致")
    return EmotionalResponseResult(
        reasons=[ProductReason.model_validate(reason) for reason in response.reasons],
        speech_text=response.speech_text,
    )
