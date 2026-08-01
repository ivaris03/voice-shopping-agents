import pytest

from voice_shopping_api.agents import workflow as workflow_module
from voice_shopping_api.agents.state import IntentResult
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
    assert first["missing_slots"] == [
        "gender",
        "size",
        "terrain",
        "cushion",
        "footType",
    ]
    assert first["pending_question"]["slot"] == "gender"

    second = await shopping_workflow.ainvoke({**first, "utterance": "男款"})
    assert second["clarification_status"] == "ASK"
    assert second["pending_question"]["slot"] == "size"
    third = await shopping_workflow.ainvoke({**second, "utterance": "42码"})
    assert third["pending_question"]["slot"] == "terrain"
    fourth = await shopping_workflow.ainvoke({**third, "utterance": "主要日常路跑"})
    assert fourth["pending_question"]["slot"] == "cushion"
    fifth = await shopping_workflow.ainvoke({**fourth, "utterance": "高缓震"})
    assert fifth["pending_question"]["slot"] == "footType"

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
        "attributes": {
            "gender": "unisex",
            "size": [36, 46],
            "terrain": "road",
            "cushion": "high",
            "footType": ["neutral"],
        },
        "selling_points": ["缓震舒适"],
        "image_urls": [],
    }
    sixth = await shopping_workflow.ainvoke(
        {**fifth, "utterance": "正常足", "catalog_products": [product]}
    )
    assert sixth["clarification_status"] == "READY"
    assert sixth["missing_slots"] == []
    assert sixth["product_cards"][0]["productId"] == product["id"]
    assert sixth["reasons"][0]["product_id"] == product["id"]

    comparison = await shopping_workflow.ainvoke(
        {
            **sixth,
            "utterance": "对比一下刚才的商品",
            "product_cards": [],
            "previous_product_cards": sixth["product_cards"],
            "catalog_products": [],
        }
    )
    assert comparison["product_cards"] == sixth["product_cards"]


@pytest.mark.asyncio
async def test_category_switch_clears_old_slots_and_routes_to_clarification() -> None:
    result = await shopping_workflow.ainvoke(
        {
            "utterance": "我想买一双鞋",
            "product_category": "HEADPHONES",
            "slots": {
                "budgetMax": 1000,
                "useCase": "commute",
                "noiseCancellation": True,
            },
            "catalog_products": [],
            "user_profile_snapshot": {
                "dynamic": {"categoryScores": {"HEADPHONES": 0.94}}
            },
        }
    )

    assert result["product_category"] == "RUNNING_SHOES"
    assert result["category_changed"] is True
    assert result["slots"] == {}
    assert result["clarification_status"] == "ASK"
    assert result["missing_slots"] == [
        "gender",
        "size",
        "terrain",
        "cushion",
        "footType",
    ]
    assert result["pending_question"]["slot"] == "gender"
    assert result.get("product_cards", []) == []


@pytest.mark.asyncio
async def test_category_switch_completes_required_slots_before_recommendation() -> None:
    first = await shopping_workflow.ainvoke(
        {
            "utterance": "我想买一双鞋",
            "product_category": "HEADPHONES",
            "slots": {
                "budgetMax": 2000,
                "useCase": "commute",
                "noiseCancellation": True,
            },
            "catalog_products": [],
            "user_profile_snapshot": {},
        }
    )
    road_product = {
        "id": "20000000-0000-4000-8000-000000000101",
        "merchant_id": "10000000-0000-4000-8000-000000000004",
        "merchant_name": "飞跃运动旗舰店",
        "name": "日常缓震跑鞋",
        "category_l2": "RUNNING_SHOES",
        "brand": "Test",
        "description": "适合日常路跑",
        "price": 899,
        "stock": 10,
        "attributes": {
            "gender": "unisex",
            "size": [36, 46],
            "terrain": "road",
            "cushion": "high",
            "footType": ["neutral"],
        },
        "selling_points": ["缓震舒适"],
        "image_urls": [],
    }
    second = await shopping_workflow.ainvoke(
        {
            **first,
            "utterance": "男款，42码，公路，高缓震，正常足",
            "catalog_products": [road_product],
        }
    )

    assert second["clarification_status"] == "READY"
    assert second["missing_slots"] == []
    assert second["slots"] == {
        "gender": "male",
        "size": 42.0,
        "terrain": "road",
        "cushion": "high",
        "footType": "neutral",
    }
    assert second["product_cards"][0]["productId"] == road_product["id"]


@pytest.mark.asyncio
async def test_model_category_switch_overrides_history_and_does_not_create_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_recognize_with_model(
        utterance: str, conversation_history: list[str]
    ) -> list[IntentResult]:
        return [
            IntentResult(
                type="PRODUCT_ORDER",
                action="CREATE",
                confidence=0.95,
                product_category="鞋子",
            )
        ]

    monkeypatch.setattr(
        workflow_module, "recognize_with_model", fake_recognize_with_model
    )
    result = await shopping_workflow.ainvoke(
        {
            "utterance": "我想买一双鞋",
            "model_enabled": True,
            "product_category": "HEADPHONES",
            "slots": {
                "budgetMax": 1000,
                "useCase": "commute",
                "noiseCancellation": True,
            },
            "catalog_products": [],
            "user_profile_snapshot": {},
        }
    )

    assert result["intents"][0]["type"] == "PRODUCT_RECOMMENDATION"
    assert result["intents"][0]["product_category"] == "RUNNING_SHOES"
    assert result["product_category"] == "RUNNING_SHOES"
    assert result["pending_question"]["slot"] == "gender"


@pytest.mark.asyncio
async def test_full_text_compliance_uses_fixed_fallback() -> None:
    result = await compliance_check({"speech_text": "这款商品百分百有效"})

    assert result["compliance_blocked"] is True
    assert result["final_reply"] == COMPLIANCE_FALLBACK
