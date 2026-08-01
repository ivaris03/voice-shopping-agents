import asyncio
import json
import re
from typing import Any

import dashscope
import httpx

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
    content = response.json()["choices"][0]["message"]["content"]
    return _parse_json(content)


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
    return [float(value) for value in response.json()["data"][0]["embedding"]]


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
            instruct="Retrieve semantically similar product facts.",
        )

    response = await asyncio.to_thread(call)
    if response.status_code != 200:
        raise RuntimeError(response.message)
    scores: dict[str, float] = {}
    for item in response.output.get("results", []):
        product = products[int(item["index"])]
        scores[str(product["id"])] = float(item["relevance_score"])
    return scores


async def recognize_with_model(
    utterance: str,
    conversation_history: list[str],
    taxonomy_categories: list[dict[str, Any]],
) -> list[IntentResult]:
    taxonomy_json = json.dumps(taxonomy_categories, ensure_ascii=False, separators=(",", ":"))
    prompt = f"""
你是电商导购意图识别 Agent。只返回 JSON 对象 {{"intents": [...]}}。
type 只能是 PRODUCT_RECOMMENDATION、PRODUCT_ORDER、PRODUCT_COMPARE、PRODUCT_QUERY、
CHAT、UNSUPPORTED_REQUEST；每项必须有 0~1 confidence。PRODUCT_ORDER 必须有
action=CREATE/CONFIRM/CANCEL。

以下是平台当前维护的完整品类与槽位配置：
{taxonomy_json}

品类规则：
1. 推荐、对比或商品查询相关意图，只能从上述配置的 categoryL2 中选择标准化
   product_category，不得创造列表外的分类。
2. requiredSlots 是该二级分类的必填槽位，optionalSlots 是选填槽位；识别分类时结合
   用户表达的商品类型与槽位语义判断，但不要在意图识别结果中输出或猜测槽位值。
3. 用户没有说明商品类型且上下文也无法确定时，不要猜测 product_category。

多意图按用户表达的语义顺序排列。不要输出解释或 Markdown。
""".strip()
    result = await _chat_json(
        prompt,
        {"utterance": utterance, "recentConversation": conversation_history[-6:]},
    )
    return [IntentResult.model_validate(item) for item in result.get("intents", [])]


async def clarify_with_model(
    utterance: str,
    product_category: str,
    required_slots: list[str],
    current_slots: dict[str, Any],
    pending_question: dict[str, Any] | None,
    slot_definitions: dict[str, dict[str, Any]],
    conversation_history: list[str],
) -> dict[str, Any]:
    prompt = """
你是电商导购的需求澄清 Agent，负责从用户本轮话语中抽取结构化槽位。
只返回 JSON 对象 {"slots": {...}}，slots 只包含本轮能够确定的新值或用户明确修正的值。

规则：
1. 优先结合 pendingQuestion 理解简短回答；pendingQuestion.slots 最多包含两个本轮正在询问的
   槽位。用户可以只回答其中一个，也可以同时回答两个，不要猜测没有回答的那一个。
2. 语音识别文本可能有同音字、近音字或断句错误。若本轮文本与待填槽位某个候选值在
   读音和语境上高度吻合且没有歧义，应纠正并输出该候选值。例如，询问入耳式还是头戴式时，
   “热辣死的”可按近音和上下文理解为“入耳式的”，输出 {"form":"in-ear"}。
3. 输出必须使用 slotDefinitions 中的标准值和类型；不得创造槽位或候选值。
4. 不要猜测用户没有表达的需求。无法可靠判断时返回空 slots。
5. 已有槽位保持不变，除非用户本轮明确改变答案。
不要输出解释、置信度、纠正后的句子或 Markdown。
""".strip()
    result = await _chat_json(
        prompt,
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
    prompt = """
你是电商情感应答 Agent。只返回 JSON 对象，结构为
{"reasons":[{"product_id":"事实中的商品ID","reason":"一条推荐理由"}],
 "speech_text":"完整语音话术"}。每个商品恰好一条理由，只能引用输入商品事实，
不得编造 ID、价格、库存、功能或认证，不得使用绝对化、医疗功效或收益承诺。
不要输出解释或 Markdown。
""".strip()
    result = await _chat_json(
        prompt,
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
