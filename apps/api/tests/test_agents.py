import pytest

from voice_shopping_api.agents.workflow import (
    COMPLIANCE_FALLBACK,
    compliance_check,
    recognize_intent,
    shopping_workflow,
)


@pytest.mark.asyncio
async def test_intents_are_returned_in_semantic_order() -> None:
    result = await recognize_intent({"utterance": "先推荐耳机，再对比一下，然后下单"})

    assert result["action_queue"] == [
        "PRODUCT_RECOMMENDATION",
        "PRODUCT_COMPARE",
        "PRODUCT_ORDER",
    ]
    assert result["intents"][2]["action"] == "CREATE"


@pytest.mark.asyncio
async def test_clarification_asks_only_one_slot_then_recommends() -> None:
    first = await shopping_workflow.ainvoke(
        {
            "utterance": "我想买双跑鞋",
            "slots": {},
            "catalog_products": [],
            "user_profile_snapshot": {},
        }
    )
    assert first["clarification_status"] == "ASK"
    assert first["missing_slots"] == ["budgetMax", "useCase"]
    assert first["pending_question"]["slot"] == "budgetMax"

    second = await shopping_workflow.ainvoke({**first, "utterance": "一千元以内"})
    assert second["clarification_status"] == "ASK"
    assert second["pending_question"]["slot"] == "useCase"

    product = {
        "id": "20000000-0000-4000-8000-000000000101",
        "merchant_id": "10000000-0000-4000-8000-000000000004",
        "merchant_name": "飞跃运动旗舰店",
        "name": "日常缓震跑鞋",
        "category_l2": "RUNNING_SHOES",
        "brand": "Test",
        "description": "适合日常路跑",
        "price": 899,
        "stock": 10,
        "attributes": {"terrain": "road"},
        "selling_points": ["缓震舒适"],
        "image_urls": [],
    }
    third = await shopping_workflow.ainvoke(
        {**second, "utterance": "主要日常路跑", "catalog_products": [product]}
    )
    assert third["clarification_status"] == "READY"
    assert third["missing_slots"] == []
    assert third["product_cards"][0]["productId"] == product["id"]
    assert third["reasons"][0]["product_id"] == product["id"]

    comparison = await shopping_workflow.ainvoke(
        {
            **third,
            "utterance": "对比一下刚才的商品",
            "product_cards": [],
            "previous_product_cards": third["product_cards"],
            "catalog_products": [],
        }
    )
    assert comparison["product_cards"] == third["product_cards"]


@pytest.mark.asyncio
async def test_full_text_compliance_uses_fixed_fallback() -> None:
    result = await compliance_check({"speech_text": "这款商品百分百有效"})

    assert result["compliance_blocked"] is True
    assert result["final_reply"] == COMPLIANCE_FALLBACK
