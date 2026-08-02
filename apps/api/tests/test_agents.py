import pytest
from langgraph.checkpoint.memory import InMemorySaver

from voice_shopping_api.agents import model as model_module
from voice_shopping_api.agents.graph import build_workflow, shopping_workflow
from voice_shopping_api.agents.nodes import clarification as clarification_module
from voice_shopping_api.agents.nodes import intent as intent_module
from voice_shopping_api.agents.nodes.clarification import clarify_requirements
from voice_shopping_api.agents.nodes.constants import COMPLIANCE_FALLBACK
from voice_shopping_api.agents.nodes.intent import recognize_intent
from voice_shopping_api.agents.nodes.response import compliance_check
from voice_shopping_api.agents.service import _handle_order
from voice_shopping_api.agents.state import (
    IntentResult,
    ShoppingWorkflowContext,
    carry_forward_state,
    state_for_persistence,
)


@pytest.mark.asyncio
async def test_intent_selects_the_first_expressed_request() -> None:
    result = await recognize_intent({"utterance": "先推荐耳机，再对比一下，然后下单"})

    assert result["intent"]["type"] == "PRODUCT_RECOMMENDATION"
    assert result["intent"]["product_category"] == "HEADPHONES"


def test_state_persistence_keeps_only_cross_turn_conversation_facts() -> None:
    state = {
        "session_id": "session-1",
        "utterance": "帮我推荐耳机",
        "conversation_history": ["user: 帮我推荐耳机"],
        "intent": {"type": "PRODUCT_RECOMMENDATION", "confidence": 0.98},
        "product_category": "HEADPHONES",
        "slots": {"form": "in-ear"},
        "pending_question": {"slot": "connectivity"},
        "catalog_products": [{"id": "candidate-1"}],
        "user_profile_snapshot": {"dynamic": {}},
        "product_cards": [{"productId": "product-1"}],
        "emotion_style": "warm-professional",
        "pending_order": {"id": "order-1"},
        "final_reply": "这是我的推荐。",
        "intents": [{"type": "CHAT"}],
        "action_queue": [],
    }

    expected = {
        "product_category": "HEADPHONES",
        "slots": {"form": "in-ear"},
        "pending_question": {"slot": "connectivity"},
        "product_cards": [{"productId": "product-1"}],
        "emotion_style": "warm-professional",
        "pending_order": {"id": "order-1"},
    }

    assert state_for_persistence(state) == expected
    assert carry_forward_state(state) == expected


@pytest.mark.asyncio
async def test_workflow_restores_state_with_a_checkpointer() -> None:
    workflow = build_workflow(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "checkpointer-test"}}

    first = await workflow.ainvoke(
        {
            "utterance": "我想买双跑鞋",
            "slots": {},
            "catalog_products": [],
            "user_profile_snapshot": {},
            "model_enabled": False,
        },
        config=config,
    )
    second = await workflow.ainvoke({"utterance": "男款，42码"}, config=config)

    assert first["pending_question"]["slots"] == ["gender", "size"]
    assert second["slots"]["gender"] == "male"
    assert second["slots"]["size"] == 42.0


@pytest.mark.asyncio
async def test_catalog_retrieval_runs_only_after_clarification_is_ready() -> None:
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
    retrievals: list[tuple[str, bool]] = []

    async def load_catalog(query: str, model_enabled: bool) -> list[dict[str, object]]:
        retrievals.append((query, model_enabled))
        return [product]

    result = await shopping_workflow.ainvoke(
        {
            "utterance": "我想买跑鞋，男款，42码，公路，高缓震，正常足",
            "slots": {},
            "user_profile_snapshot": {},
            "model_enabled": False,
        },
        context=ShoppingWorkflowContext(catalog_loader=load_catalog),
    )

    assert retrievals == [("我想买跑鞋，男款，42码，公路，高缓震，正常足", False)]
    assert result["clarification_status"] == "READY"
    assert result["product_cards"][0]["productId"] == product["id"]


@pytest.mark.asyncio
async def test_order_node_executes_the_context_order_handler() -> None:
    handled_states: list[dict[str, object]] = []

    async def load_catalog(_: str, __: bool) -> list[dict[str, object]]:
        return []

    async def handle_order(state: dict[str, object]) -> dict[str, object]:
        handled_states.append(state)
        return {
            "pending_order": {"id": "order-1", "status": "pending"},
            "speech_text": "已生成待确认订单。",
            "final_reply": "已生成待确认订单。",
            "compliance_blocked": False,
        }

    result = await shopping_workflow.ainvoke(
        {
            "utterance": "下单",
            "previous_product_cards": [{"productId": "product-1", "name": "测试商品"}],
            "model_enabled": False,
        },
        context=ShoppingWorkflowContext(
            catalog_loader=load_catalog,
            order_handler=handle_order,
        ),
    )

    assert handled_states[0]["intent"]["type"] == "PRODUCT_ORDER"
    assert result["pending_order"]["id"] == "order-1"


@pytest.mark.asyncio
async def test_model_order_without_recommendations_returns_to_requirement_clarification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_recognize_with_model(
        utterance: str,
        conversation_history: list[str],
        taxonomy_categories: list[dict[str, object]],
    ) -> IntentResult:
        return IntentResult(
            type="PRODUCT_ORDER",
            action="CREATE",
            confidence=0.95,
            product_category="RUNNING_SHOES",
        )

    monkeypatch.setattr(intent_module, "recognize_with_model", fake_recognize_with_model)
    result = await shopping_workflow.ainvoke(
        {
            "utterance": "我想买一双通勤鞋",
            # A reused session may know the category but still have no displayed products.
            "product_category": "RUNNING_SHOES",
            "previous_product_cards": [],
            "slots": {},
            "model_enabled": True,
        }
    )

    assert result["intent"]["type"] == "PRODUCT_RECOMMENDATION"
    assert result["clarification_status"] == "ASK"
    assert result["pending_question"]["slots"] == ["gender", "size"]


@pytest.mark.asyncio
async def test_order_handler_does_not_request_a_product_position_without_cards() -> None:
    expected_reply = "我还没有给你展示推荐商品。请先告诉我想买什么，我会为你筛选合适的商品。"
    result = await _handle_order(
        None,  # type: ignore[arg-type]
        {
            "intent": {"type": "PRODUCT_ORDER", "action": "CREATE"},
            "utterance": "下单",
            "previous_product_cards": [],
        },
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
    )

    assert result["final_reply"] == expected_reply


@pytest.mark.asyncio
async def test_clarification_asks_up_to_two_slots_then_recommends() -> None:
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
    assert first["pending_question"]["slots"] == ["gender", "size"]
    assert first["pending_question"]["question"] == (
        "你需要男款、女款还是中性款？另外，你需要多大尺码？"
    )

    second = await shopping_workflow.ainvoke({**first, "utterance": "男款，42码"})
    assert second["clarification_status"] == "ASK"
    assert second["pending_question"]["slots"] == ["terrain", "cushion"]
    third = await shopping_workflow.ainvoke({**second, "utterance": "公路，高缓震"})
    assert third["pending_question"]["slots"] == ["footType"]

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
            "sizeRange": [36, 46],
            "terrain": "road",
            "cushion": "high",
            "footType": ["neutral"],
        },
        "selling_points": ["缓震舒适"],
        "image_urls": [],
    }
    fourth = await shopping_workflow.ainvoke(
        {**third, "utterance": "正常足", "catalog_products": [product]}
    )
    assert fourth["clarification_status"] == "READY"
    assert fourth["missing_slots"] == []
    assert fourth["product_cards"][0]["productId"] == product["id"]
    assert fourth["reasons"][0]["product_id"] == product["id"]

    comparison = await shopping_workflow.ainvoke(
        {
            **fourth,
            "utterance": "对比一下刚才的商品",
            "product_cards": [],
            "previous_product_cards": fourth["product_cards"],
            "catalog_products": [],
        }
    )
    assert comparison["product_cards"] == fourth["product_cards"]


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
            "user_profile_snapshot": {"dynamic": {"categoryScores": {"HEADPHONES": 0.94}}},
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
    assert result["pending_question"]["slots"] == ["gender", "size"]
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
        utterance: str,
        conversation_history: list[str],
        taxonomy_categories: list[dict[str, object]],
    ) -> IntentResult:
        return IntentResult(
            type="PRODUCT_ORDER",
            action="CREATE",
            confidence=0.95,
            product_category="鞋子",
        )

    async def fake_clarify_with_model(*args: object) -> dict[str, object]:
        return {}

    monkeypatch.setattr(intent_module, "recognize_with_model", fake_recognize_with_model)
    monkeypatch.setattr(clarification_module, "clarify_with_model", fake_clarify_with_model)
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

    assert result["intent"]["type"] == "PRODUCT_RECOMMENDATION"
    assert result["intent"]["product_category"] == "RUNNING_SHOES"
    assert result["product_category"] == "RUNNING_SHOES"
    assert result["pending_question"]["slot"] == "gender"
    assert result["pending_question"]["slots"] == ["gender", "size"]


@pytest.mark.asyncio
async def test_intent_system_prompt_contains_all_category_slot_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_chat_json(system_prompt: str, payload: dict[str, object]) -> dict[str, object]:
        captured["system_prompt"] = system_prompt
        captured["payload"] = payload
        return {
            "intent": {
                "type": "PRODUCT_RECOMMENDATION",
                "confidence": 0.98,
                "product_category": "HEADPHONES",
            }
        }

    monkeypatch.setattr(model_module, "_chat_json", fake_chat_json)
    categories = [
        {
            "categoryL1": "ELECTRONICS",
            "categoryL2": "HEADPHONES",
            "requiredSlots": ["form", "connectivity"],
            "optionalSlots": ["noiseCancellation", "batteryHours"],
            "slots": [
                {"key": "form", "isRequired": True, "enumValues": ["in-ear", "over-ear"]},
                {
                    "key": "connectivity",
                    "isRequired": True,
                    "enumValues": ["bluetooth", "wired"],
                },
                {
                    "key": "noiseCancellation",
                    "isRequired": False,
                    "enumValues": [True, False],
                },
                {
                    "key": "batteryHours",
                    "isRequired": False,
                    "enumValues": [8, 24, 60],
                },
            ],
        },
        {
            "categoryL1": "SPORTS",
            "categoryL2": "RUNNING_SHOES",
            "requiredSlots": ["gender", "size", "terrain"],
            "optionalSlots": ["cushion", "footType"],
            "slots": [
                {
                    "key": "gender",
                    "isRequired": True,
                    "enumValues": ["male", "female", "unisex"],
                },
                {"key": "size", "isRequired": True, "enumValues": [40, 41, 42]},
                {"key": "terrain", "isRequired": True, "enumValues": ["road", "trail"]},
            ],
        },
    ]

    await model_module.recognize_with_model("推荐耳机", [], categories)

    prompt = str(captured["system_prompt"])
    assert '"categoryL2":"HEADPHONES"' in prompt
    assert '"requiredSlots":["form","connectivity"]' in prompt
    assert '"optionalSlots":["noiseCancellation","batteryHours"]' in prompt
    assert '"key":"form","isRequired":true,"enumValues":["in-ear","over-ear"]' in prompt
    assert '"key":"connectivity","isRequired":true,"enumValues":["bluetooth","wired"]' in prompt
    assert '"key":"noiseCancellation","isRequired":false,"enumValues":[true,false]' in prompt
    assert '"key":"batteryHours","isRequired":false,"enumValues":[8,24,60]' in prompt
    assert '"categoryL2":"RUNNING_SHOES"' in prompt
    assert categories not in captured["payload"].values()


@pytest.mark.asyncio
async def test_clarification_agent_resolves_contextual_asr_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_clarify_with_model(
        utterance: str,
        product_category: str,
        required_slots: list[str],
        current_slots: dict[str, object],
        pending_question: dict[str, object] | None,
        slot_definitions: dict[str, dict[str, object]],
        conversation_history: list[str],
    ) -> dict[str, object]:
        captured.update(
            utterance=utterance,
            product_category=product_category,
            pending_question=pending_question,
            slot_definitions=slot_definitions,
        )
        return {"form": "in-ear"}

    monkeypatch.setattr(clarification_module, "clarify_with_model", fake_clarify_with_model)
    result = await clarify_requirements(
        {
            "utterance": "想要热辣死的。",
            "model_enabled": True,
            "product_category": "HEADPHONES",
            "category_changed": False,
            "slots": {"noiseCancellation": True},
            "pending_question": {
                "slot": "form",
                "question": "你想要入耳式还是头戴式？",
            },
            "conversation_history": ["assistant: 你想要入耳式还是头戴式？"],
        }
    )

    assert captured["utterance"] == "想要热辣死的。"
    assert captured["pending_question"] == {
        "slot": "form",
        "question": "你想要入耳式还是头戴式？",
    }
    assert result["slots"] == {"noiseCancellation": True, "form": "in-ear"}
    assert result["pending_question"]["slot"] == "connectivity"
    assert result["pending_question"]["slots"] == ["connectivity", "batteryHours"]


@pytest.mark.asyncio
async def test_clarification_agent_receives_numeric_shoe_size_definition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_clarify_with_model(
        utterance: str,
        product_category: str,
        required_slots: list[str],
        current_slots: dict[str, object],
        pending_question: dict[str, object] | None,
        slot_definitions: dict[str, dict[str, object]],
        conversation_history: list[str],
    ) -> dict[str, object]:
        captured["slot_definitions"] = slot_definitions
        return {"size": 42}

    monkeypatch.setattr(clarification_module, "clarify_with_model", fake_clarify_with_model)
    result = await clarify_requirements(
        {
            "utterance": "四十二码",
            "model_enabled": True,
            "product_category": "RUNNING_SHOES",
            "category_changed": False,
            "slots": {},
            "required_slots_by_category": {"RUNNING_SHOES": ["size"]},
            "allowed_slots_by_category": {"RUNNING_SHOES": ["size"]},
            "taxonomy_slot_definitions_by_category": {
                "RUNNING_SHOES": {"size": {"type": "enum", "values": [35, 36, 42]}}
            },
        }
    )

    size_definition = captured["slot_definitions"]["size"]
    assert size_definition["type"] == "number"
    assert size_definition["productAttribute"] == "sizeRange"
    assert size_definition["matchMode"] == "range_contains"
    assert result["slots"] == {"size": 42}


@pytest.mark.asyncio
async def test_clarification_agent_rejects_unknown_or_invalid_slot_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_clarify_with_model(*args: object) -> dict[str, object]:
        return {"form": "speaker", "inventedSlot": True}

    monkeypatch.setattr(clarification_module, "clarify_with_model", fake_clarify_with_model)
    result = await clarify_requirements(
        {
            "utterance": "随便吧",
            "model_enabled": True,
            "product_category": "HEADPHONES",
            "category_changed": False,
            "slots": {"noiseCancellation": True},
            "pending_question": {
                "slot": "form",
                "question": "你想要入耳式还是头戴式？",
            },
        }
    )

    assert result["slots"] == {"noiseCancellation": True}
    assert result["pending_question"]["slot"] == "form"
    assert result["pending_question"]["slots"] == ["form", "connectivity"]


@pytest.mark.asyncio
async def test_answering_second_question_does_not_pollute_first_slot() -> None:
    result = await clarify_requirements(
        {
            "utterance": "想要一个蓝牙的。",
            "model_enabled": False,
            "product_category": "HEADPHONES",
            "category_changed": False,
            "required_slots_by_category": {"HEADPHONES": ["form", "connectivity"]},
            "allowed_slots_by_category": {"HEADPHONES": ["form", "connectivity"]},
            "taxonomy_slot_definitions": {
                "form": {"type": "enum", "values": ["in-ear", "over-ear"]},
                "connectivity": {"type": "enum", "values": ["bluetooth", "wired"]},
            },
            "slots": {"form": "想要一个蓝牙的。", "connectivity": "bluetooth"},
            "pending_question": {
                "slot": "form",
                "slots": ["form", "connectivity"],
                "question": "你想要入耳式还是头戴式？另外，你希望使用蓝牙还是有线连接？",
            },
        }
    )

    assert result["slots"] == {"connectivity": "bluetooth"}
    assert result["clarification_status"] == "ASK"
    assert result["pending_question"]["slots"] == ["form"]


@pytest.mark.asyncio
async def test_dynamic_enum_slot_can_be_answered_without_model() -> None:
    result = await clarify_requirements(
        {
            "utterance": "blue",
            "model_enabled": False,
            "product_category": "CUSTOM_ITEM",
            "category_changed": False,
            "required_slots_by_category": {"CUSTOM_ITEM": ["color"]},
            "allowed_slots_by_category": {"CUSTOM_ITEM": ["color"]},
            "taxonomy_slot_definitions_by_category": {
                "CUSTOM_ITEM": {"color": {"type": "enum", "values": ["red", "blue"]}}
            },
            "slots": {},
            "pending_question": {
                "slot": "color",
                "slots": ["color"],
                "question": "请告诉我color？",
            },
        }
    )

    assert result["slots"] == {"color": "blue"}
    assert result["clarification_status"] == "READY"


@pytest.mark.asyncio
async def test_full_text_compliance_uses_fixed_fallback() -> None:
    result = await compliance_check({"speech_text": "这款商品百分百有效"})

    assert result["compliance_blocked"] is True
    assert result["final_reply"] == COMPLIANCE_FALLBACK


@pytest.mark.asyncio
async def test_response_model_uses_streaming_json_when_a_delta_handler_is_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[str] = []

    async def collect(delta: str) -> None:
        received.append(delta)

    async def fake_stream_chat_json(
        system_prompt: str,
        payload: dict[str, object],
        on_speech_delta: object,
    ) -> dict[str, object]:
        assert "speech_text" in system_prompt
        assert payload["utterance"] == "推荐耳机"
        assert callable(on_speech_delta)
        await on_speech_delta("正在为你筛选。")  # type: ignore[operator]
        return {
            "speech_text": "正在为你筛选。",
            "reasons": [{"product_id": "product-1", "reason": "适合通勤。"}],
        }

    monkeypatch.setattr(model_module, "_stream_chat_json", fake_stream_chat_json)
    result = await model_module.respond_with_model(
        "推荐耳机",
        [{"productId": "product-1"}],
        "warm-professional",
        collect,
    )

    assert received == ["正在为你筛选。"]
    assert result.speech_text == "正在为你筛选。"
