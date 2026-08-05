import asyncio
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.runtime import Runtime

from voice_shopping_api.agents import graph as graph_module
from voice_shopping_api.agents import model as model_module
from voice_shopping_api.agents import service as service_module
from voice_shopping_api.agents.graph import build_workflow, shopping_workflow
from voice_shopping_api.agents.nodes import clarification as clarification_module
from voice_shopping_api.agents.nodes import intent as intent_module
from voice_shopping_api.agents.nodes import recommendation as recommendation_module
from voice_shopping_api.agents.nodes import response as response_module
from voice_shopping_api.agents.nodes.clarification import clarify_requirements
from voice_shopping_api.agents.nodes.constants import COMPLIANCE_FALLBACK, REQUIRED_SLOTS
from voice_shopping_api.agents.nodes.intent import recognize_intent
from voice_shopping_api.agents.nodes.recommendation import recommend_products
from voice_shopping_api.agents.nodes.response import (
    compliance_check,
    compliance_node,
    emotional_response,
    publish_response,
    violation_response,
)
from voice_shopping_api.agents.service import _handle_order, state_events
from voice_shopping_api.agents.state import (
    IntentResult,
    ProductReason,
    RecommendationHook,
    ShoppingInputState,
    ShoppingOutputState,
    ShoppingRuntimeDependencies,
    carry_forward_state,
    state_for_persistence,
)


@pytest.mark.asyncio
async def test_intent_selects_the_first_expressed_request() -> None:
    result = await recognize_intent({"utterance": "先推荐耳机，再对比一下，然后下单"})

    assert result["intent"]["type"] == "PRODUCT_RECOMMENDATION"
    assert result["intent"]["product_category"] == "HEADPHONES"


@pytest.mark.asyncio
async def test_selected_recommendation_routes_to_create_order_before_category_matching() -> None:
    utterance = "买这个第二款耳机吧，你来帮我下单吧。"
    result = await recognize_intent(
        {
            "utterance": utterance,
            "model_enabled": False,
            "previous_product_cards": [
                {"productId": "product-1", "name": "AirPods Pro 2"},
                {"productId": "product-2", "name": "Edifier"},
            ],
        }
    )

    assert result["intent"] == {
        "type": "PRODUCT_ORDER",
        "confidence": 0.99,
        "action": "CREATE",
    }
    assert result["starts_new_product_request"] is False


@pytest.mark.asyncio
async def test_intent_normalizes_category_and_tracks_category_change() -> None:
    updates = await recognize_intent(
        {
            "utterance": "我想买一双跑鞋",
            "model_enabled": False,
            "product_category": "HEADPHONES",
        }
    )

    assert updates["intent"]["type"] == "PRODUCT_RECOMMENDATION"
    assert updates["intent"]["product_category"] == "RUNNING_SHOES"
    assert updates["product_category"] == "RUNNING_SHOES"
    assert updates["category_changed"] is True


@pytest.mark.asyncio
async def test_pending_question_skips_intent_recognition_and_completes_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slot answer resumes clarification instead of starting a second intent turn."""

    intent_calls: list[str] = []

    async def fake_recognize_with_model(*_: object) -> IntentResult:
        intent_calls.append("called")
        return IntentResult(type="CHAT", confidence=0.91)

    async def fake_clarify_with_model(*_: object) -> dict[str, object]:
        return {}

    async def fake_rerank_products(*_: object) -> dict[str, float]:
        return {"watch-1": 0.8}

    async def fake_product_reason(*_: object) -> ProductReason:
        return ProductReason(product_id="watch-1", reason="自动机械机芯符合你的偏好。")

    product = {
        "id": "watch-1",
        "merchant_id": "merchant-1",
        "merchant_name": "测试腕表店",
        "name": "测试机械腕表",
        "brand": "Test",
        "description": "自动机械机芯",
        "price": 2280,
        "stock": 10,
        "attributes": {"movement": "automatic"},
        "selling_points": ["自动机械机芯"],
        "image_urls": [],
    }

    async def load_catalog(
        _: str, __: bool, filters: dict[str, object]
    ) -> list[dict[str, object]]:
        assert filters["slots"] == {"movement": "automatic"}
        return [product]

    monkeypatch.setattr(intent_module, "recognize_with_model", fake_recognize_with_model)
    monkeypatch.setattr(clarification_module, "clarify_with_model", fake_clarify_with_model)
    monkeypatch.setattr(recommendation_module, "rerank_products", fake_rerank_products)
    monkeypatch.setattr(response_module, "generate_product_reason", fake_product_reason)
    result = await shopping_workflow.ainvoke(
        {
            "utterance": "嗯其嗯，机械的吧。",
            "model_enabled": True,
            "product_category": "WATCHES",
            "slots": {},
            "pending_question": {
                "slot": "movement",
                "slots": ["movement"],
                "question": "你偏好机械、石英还是光动能机芯？",
            },
            "required_slots_by_category": {"WATCHES": ["movement"]},
            "allowed_slots_by_category": {"WATCHES": ["movement"]},
            "taxonomy_slot_definitions_by_category": {
                "WATCHES": {
                    "movement": {
                        "type": "enum",
                        "values": ["automatic", "quartz", "eco-drive"],
                    }
                }
            },
            "user_profile_snapshot": {},
        },
        context=ShoppingRuntimeDependencies(catalog_loader=load_catalog),
    )

    assert intent_calls == []
    assert result.get("intent") is None
    assert result["clarification_status"] == "READY"
    assert result["slots"] == {"movement": "automatic"}
    assert result["pending_question"] is None
    assert result["product_cards"][0]["productId"] == product["id"]


@pytest.mark.asyncio
async def test_explicit_slot_answer_is_not_overwritten_by_conflicting_model_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_clarify_with_model(*_: object) -> dict[str, object]:
        return {"movement": "quartz"}

    monkeypatch.setattr(clarification_module, "clarify_with_model", fake_clarify_with_model)
    result = await clarify_requirements(
        {
            "utterance": "嗯其嗯，机械的吧。",
            "model_enabled": True,
            "product_category": "WATCHES",
            "slots": {},
            "pending_question": {
                "slot": "movement",
                "slots": ["movement"],
                "question": "你偏好机械、石英还是光动能机芯？",
            },
            "required_slots_by_category": {"WATCHES": ["movement"]},
            "allowed_slots_by_category": {"WATCHES": ["movement"]},
            "taxonomy_slot_definitions_by_category": {
                "WATCHES": {
                    "movement": {
                        "type": "enum",
                        "values": ["automatic", "quartz", "eco-drive"],
                    }
                }
            },
        }
    )

    assert result["slots"] == {"movement": "automatic"}
    assert result["clarification_status"] == "READY"


@pytest.mark.asyncio
async def test_model_query_for_explicit_purchase_routes_through_clarification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Noisy ASR must not let a query label bypass new-product clarification."""

    async def fake_recognize_with_model(*_: object) -> IntentResult:
        return IntentResult(
            type="PRODUCT_QUERY",
            confidence=0.95,
            product_category="HEADPHONES",
        )

    async def fake_clarify_with_model(*_: object) -> dict[str, object]:
        return {}

    async def fake_rerank_products(*_: object) -> dict[str, float]:
        return {"headphone-1": 0.8}

    async def fake_product_reason(*_: object) -> ProductReason:
        return ProductReason(product_id="headphone-1", reason="符合头戴式蓝牙需求。")

    product = {
        "id": "headphone-1",
        "merchant_id": "merchant-1",
        "merchant_name": "测试数码店",
        "name": "测试头戴式蓝牙耳机",
        "brand": "Test",
        "description": "头戴式蓝牙耳机",
        "price": 999,
        "stock": 10,
        "attributes": {"form": "over-ear", "connectivity": "bluetooth"},
        "selling_points": ["头戴式蓝牙连接"],
        "image_urls": [],
    }
    retrievals: list[dict[str, object]] = []

    async def load_catalog(
        _: str, __: bool, filters: dict[str, object]
    ) -> list[dict[str, object]]:
        retrievals.append(filters)
        return [product]

    monkeypatch.setattr(intent_module, "recognize_with_model", fake_recognize_with_model)
    monkeypatch.setattr(clarification_module, "clarify_with_model", fake_clarify_with_model)
    monkeypatch.setattr(recommendation_module, "rerank_products", fake_rerank_products)
    monkeypatch.setattr(response_module, "generate_product_reason", fake_product_reason)
    result = await shopping_workflow.ainvoke(
        {
            "utterance": "我要买一个。嗯。口袋的头戴的耳机、蓝牙耳机吧头戴的耳机，蓝牙耳机吧。",
            "model_enabled": True,
            "product_category": "RUNNING_SHOES",
            "slots": {"gender": "male", "size": 42, "terrain": "road"},
            "required_slots_by_category": {"HEADPHONES": ["form", "connectivity"]},
            "allowed_slots_by_category": {
                "HEADPHONES": ["form", "connectivity", "noiseCancellation", "batteryHours"]
            },
            "taxonomy_slot_definitions_by_category": {
                "HEADPHONES": {
                    "form": {"type": "enum", "values": ["in-ear", "over-ear"]},
                    "connectivity": {"type": "enum", "values": ["bluetooth", "wired"]},
                }
            },
            "user_profile_snapshot": {"dynamic": {}},
        },
        context=ShoppingRuntimeDependencies(catalog_loader=load_catalog),
    )

    assert result["intent"]["type"] == "PRODUCT_RECOMMENDATION"
    assert result["slots"] == {"form": "over-ear", "connectivity": "bluetooth"}
    assert retrievals[0]["slots"] == {"form": "over-ear", "connectivity": "bluetooth"}
    assert result["product_cards"][0]["productId"] == product["id"]


@pytest.mark.asyncio
async def test_model_query_category_switch_clears_old_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_recognize_with_model(*_: object) -> IntentResult:
        return IntentResult(
            type="PRODUCT_QUERY",
            confidence=0.95,
            product_category="HEADPHONES",
        )

    monkeypatch.setattr(intent_module, "recognize_with_model", fake_recognize_with_model)
    result = await recognize_intent(
        {
            "utterance": "耳机怎么样？",
            "model_enabled": True,
            "product_category": "RUNNING_SHOES",
            "slots": {"gender": "male", "size": 42, "terrain": "road"},
        }
    )

    assert result["intent"]["type"] == "PRODUCT_QUERY"
    assert result["category_changed"] is True
    assert result["slots"] == {}
    assert result["pending_question"] is None


@pytest.mark.asyncio
async def test_retrieval_ignores_slots_outside_current_category() -> None:
    retrievals: list[dict[str, object]] = []

    async def load_catalog(
        _: str, __: bool, filters: dict[str, object]
    ) -> list[dict[str, object]]:
        retrievals.append(filters)
        return []

    await recommend_products(
        {
            "utterance": "耳机多少钱？",
            "intent": {"type": "PRODUCT_QUERY", "confidence": 0.95},
            "model_enabled": False,
            "product_category": "HEADPHONES",
            "slots": {"gender": "male", "size": 42, "terrain": "road", "form": "over-ear"},
            "allowed_slots_by_category": {
                "HEADPHONES": ["form", "connectivity", "noiseCancellation", "batteryHours"]
            },
            "required_slots_by_category": {"HEADPHONES": ["form", "connectivity"]},
            "user_profile_snapshot": {},
        },
        Runtime(context=ShoppingRuntimeDependencies(catalog_loader=load_catalog)),
    )

    assert retrievals[0]["slots"] == {"form": "over-ear"}


@pytest.mark.asyncio
async def test_same_category_new_request_discards_previous_requirements() -> None:
    result = await shopping_workflow.ainvoke(
        {
            "utterance": "嗯，我要买一双鞋。",
            "product_category": "RUNNING_SHOES",
            "slots": {"gender": "unisex", "size": 42, "terrain": "road"},
            "pending_question": None,
            "previous_product_cards": [
                {"productId": "old-1", "name": "旧推荐 1"},
                {"productId": "old-2", "name": "旧推荐 2"},
                {"productId": "old-3", "name": "旧推荐 3"},
            ],
            "model_enabled": False,
            "user_profile_snapshot": {},
        }
    )

    assert result["clarification_status"] == "ASK"
    assert result["slots"] == {}
    assert result["pending_question"]["slots"] == ["gender", "size"]
    assert result.get("product_cards", []) == []


@pytest.mark.asyncio
async def test_slot_answers_keep_memory_and_recommend_when_requirements_are_complete() -> None:
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
        "attributes": {"gender": "unisex", "sizeRange": [36, 46], "terrain": "road"},
        "selling_points": ["缓震舒适"],
        "image_urls": [],
    }

    retrievals: list[dict[str, object]] = []

    async def load_catalog(
        _: str, __: bool, filters: dict[str, object]
    ) -> list[dict[str, object]]:
        retrievals.append(filters)
        return [product]

    context = ShoppingRuntimeDependencies(catalog_loader=load_catalog)
    first = await shopping_workflow.ainvoke(
        {
            "utterance": "我要买一双鞋。",
            "slots": {},
            "model_enabled": False,
            "user_profile_snapshot": {},
            "required_slots_by_category": {"RUNNING_SHOES": ["gender", "size", "terrain"]},
            "allowed_slots_by_category": {
                "RUNNING_SHOES": ["gender", "size", "terrain", "cushion", "footType"]
            },
        },
        context=context,
    )
    second = await shopping_workflow.ainvoke(
        {
            **first,
            "utterance": "我想要中性款时尚尺码的鞋。",
            "model_enabled": False,
            "product_cards": [],
            "previous_product_cards": [],
        },
        context=context,
    )
    assert retrievals == []
    third = await shopping_workflow.ainvoke(
        {
            **second,
            "utterance": "我需要42尺码，然后是公路的鞋。",
            "model_enabled": False,
            "product_cards": [],
            "previous_product_cards": [],
        },
        context=context,
    )

    assert second["slots"] == {"gender": "unisex"}
    assert second["pending_question"]["slots"] == ["size", "terrain"]
    assert third["clarification_status"] == "READY"
    assert third["slots"] == {"gender": "unisex", "size": 42.0, "terrain": "road"}
    assert third["product_cards"][0]["productId"] == product["id"]
    assert len(retrievals) == 1


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
    }

    assert state_for_persistence(state) == expected
    assert carry_forward_state(state) == expected


def test_workflow_declares_explicit_langgraph_io_and_context_schemas() -> None:
    input_properties = shopping_workflow.get_input_jsonschema()["properties"]
    output_properties = shopping_workflow.get_output_jsonschema()["properties"]

    assert set(input_properties) == set(ShoppingInputState.__annotations__)
    assert set(output_properties) == set(ShoppingOutputState.__annotations__)
    assert shopping_workflow.context_schema is ShoppingRuntimeDependencies
    assert "taxonomy_categories" in input_properties
    assert "taxonomy_categories" not in output_properties
    assert "catalog_products" not in output_properties
    assert "final_reply" in output_properties


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
async def test_recommendation_agent_retrieves_after_clarification_is_ready() -> None:
    product = {
        "id": "20000000-0000-4000-8000-000000000101",
        "merchant_id": "10000000-0000-4000-8000-000000000004",
        "merchant_name": "飞跃运动旗舰店",
        "sku": "RUN-TEST-001",
        "name": "日常缓震跑鞋",
        "category_l1": "SPORTS",
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
        "created_at": "2026-08-04T11:47:05.198677Z",
        "updated_at": "2026-08-04T13:48:47.279999Z",
    }
    retrievals: list[tuple[str, bool, dict[str, object]]] = []

    async def load_catalog(
        query: str, model_enabled: bool, filters: dict[str, object]
    ) -> list[dict[str, object]]:
        retrievals.append((query, model_enabled, filters))
        return [product]

    result = await shopping_workflow.ainvoke(
        {
            "utterance": "我想买跑鞋，男款，42码，公路，高缓震，正常足",
            "slots": {},
            "user_profile_snapshot": {},
            "model_enabled": False,
        },
        context=ShoppingRuntimeDependencies(catalog_loader=load_catalog),
    )

    query, model_enabled, filters = retrievals[0]
    assert query == "我想买跑鞋，男款，42码，公路，高缓震，正常足"
    assert model_enabled is False
    assert filters["category"] == "RUNNING_SHOES"
    assert filters["required_slots"] == list(REQUIRED_SLOTS["RUNNING_SHOES"])
    assert set(filters["slots"]) >= {"gender", "size", "terrain"}
    assert result["clarification_status"] == "READY"
    assert result["product_cards"][0]["productId"] == product["id"]
    card = result["product_cards"][0]
    assert card["sku"] == product["sku"]
    assert card["categoryL1"] == product["category_l1"]
    assert card["categoryL2"] == product["category_l2"]
    assert card["description"] == product["description"]
    assert card["attributes"] == product["attributes"]
    assert card["createdAt"] == product["created_at"]
    assert card["updatedAt"] == product["updated_at"]


@pytest.mark.asyncio
async def test_order_node_executes_the_context_order_handler() -> None:
    handled_states: list[dict[str, object]] = []

    async def load_catalog(_: str, __: bool, _filters: object) -> list[dict[str, object]]:
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
        context=ShoppingRuntimeDependencies(
            catalog_loader=load_catalog,
            order_handler=handle_order,
        ),
    )

    assert handled_states[0]["intent"]["type"] == "PRODUCT_ORDER"
    assert result["pending_order"]["id"] == "order-1"


@pytest.mark.asyncio
async def test_selected_product_order_creates_a_pending_order_before_confirmation() -> None:
    handled_states: list[dict[str, object]] = []

    async def load_catalog(_: str, __: bool, _filters: object) -> list[dict[str, object]]:
        return []

    async def handle_order(state: dict[str, object]) -> dict[str, object]:
        handled_states.append(state)
        return {
            "pending_order": {"id": "order-2", "status": "pending"},
            "speech_text": "已生成待确认订单，请说确认下单或取消订单。",
            "final_reply": "已生成待确认订单，请说确认下单或取消订单。",
            "compliance_blocked": False,
        }

    result = await shopping_workflow.ainvoke(
        {
            "utterance": "买这个第二款耳机吧，你来帮我下单吧。",
            "previous_product_cards": [
                {"productId": "product-1", "name": "AirPods Pro 2"},
                {"productId": "product-2", "name": "Edifier"},
            ],
            "model_enabled": False,
        },
        context=ShoppingRuntimeDependencies(
            catalog_loader=load_catalog,
            order_handler=handle_order,
        ),
    )

    assert handled_states[0]["intent"] == {
        "type": "PRODUCT_ORDER",
        "confidence": 0.99,
        "action": "CREATE",
    }
    assert result["pending_order"]["status"] == "pending"
    assert "确认下单" in result["final_reply"]


@pytest.mark.asyncio
async def test_confirmation_requires_an_existing_pending_order() -> None:
    result = await recognize_intent(
        {
            "utterance": "买第二款耳机，下单吧。",
            "model_enabled": False,
            "previous_product_cards": [{"productId": "product-2", "name": "Edifier"}],
        }
    )
    confirmation = await recognize_intent(
        {
            "utterance": "确认下单。",
            "model_enabled": False,
            "pending_order": {"id": "order-2", "status": "pending"},
        }
    )

    assert result["intent"]["action"] == "CREATE"
    assert confirmation["intent"]["action"] == "CONFIRM"


@pytest.mark.asyncio
async def test_order_handler_uses_the_selected_product_and_requests_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_product_id = UUID("20000000-0000-4000-8000-000000000001")
    second_product_id = UUID("20000000-0000-4000-8000-000000000002")
    captured: dict[str, object] = {}

    async def no_pending_order(*_: object) -> None:
        return None

    async def create_order(*args: object) -> dict[str, object]:
        payload = args[2]
        captured["product_id"] = payload.product_id
        return {
            "id": "30000000-0000-4000-8000-000000000001",
            "status": "pending",
            "product_snapshot": {"name": "Edifier 入门真无线耳机"},
            "unit_price": 199,
            "total_amount": 199,
        }

    monkeypatch.setattr(service_module, "_latest_pending_order_id", no_pending_order)
    monkeypatch.setattr(service_module, "create_pending_order", create_order)
    result = await _handle_order(
        None,  # type: ignore[arg-type]
        {
            "intent": {"type": "PRODUCT_ORDER", "action": "CREATE"},
            "utterance": "买这个第二款耳机吧，你来帮我下单吧。",
            "previous_product_cards": [
                {"productId": str(first_product_id), "name": "AirPods Pro 2"},
                {"productId": str(second_product_id), "name": "Edifier 入门真无线耳机"},
            ],
        },
        None,  # type: ignore[arg-type]
        UUID("40000000-0000-4000-8000-000000000001"),
        UUID("50000000-0000-4000-8000-000000000001"),
    )

    assert captured["product_id"] == second_product_id
    assert result["pending_order"]["status"] == "pending"
    assert result["final_reply"] == (
        "已生成待确认订单：Edifier 入门真无线耳机，数量 1，单价 199 元，"
        "合计 199 元。订单十五分钟内有效，请说确认下单或取消订单。"
    )


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
            "user_profile_snapshot": {
                "dynamic": {"categoryAffinity": {"HEADPHONES": 0.94}}
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

    async def fake_structured_chat(
        system_prompt: str, payload: dict[str, object], schema: type[object]
    ) -> IntentResult:
        captured["system_prompt"] = system_prompt
        captured["payload"] = payload
        assert schema is IntentResult
        return IntentResult(
            type="PRODUCT_RECOMMENDATION",
            confidence=0.98,
            product_category="HEADPHONES",
        )

    monkeypatch.setattr(model_module, "_structured_chat", fake_structured_chat)
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
async def test_sentence_compliance_routes_to_violation_response() -> None:
    result = await compliance_check({"speech_text": "这款商品百分百有效"})

    assert result["compliance_blocked"] is True
    assert result["violation_sentence"] == "这款商品百分百有效"
    violation = await violation_response(result)
    assert violation["final_reply"] == COMPLIANCE_FALLBACK


@pytest.mark.asyncio
async def test_sentence_compliance_checks_each_completed_sentence() -> None:
    result = await compliance_check({"speech_text": "第一句安全。第二句百分百有效。第三句安全。"})

    assert result["compliance_blocked"] is True
    assert result["violation_sentence"] == "第二句百分百有效。"


@pytest.mark.asyncio
async def test_graph_routes_violation_before_publishing_original_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_emotional_response(
        state: dict[str, object], runtime: Runtime[ShoppingRuntimeDependencies]
    ) -> dict[str, object]:
        assert runtime.context is not None
        return {
            "speech_text": "第一句安全。第二句百分百有效。",
            "final_reply": "第一句安全。第二句百分百有效。",
        }

    async def load_catalog(
        _: str, __: bool, ___: dict[str, object]
    ) -> list[dict[str, object]]:
        return []

    deltas: list[str] = []
    sentences: list[str] = []

    async def publish_delta(value: str) -> None:
        deltas.append(value)

    async def publish_sentence(value: str, _sentence_index: int, _sentence_count: int) -> None:
        sentences.append(value)

    monkeypatch.setattr(graph_module, "emotional_response", fake_emotional_response)
    workflow = build_workflow()
    result = await workflow.ainvoke(
        {"utterance": "你好", "model_enabled": False},
        context=ShoppingRuntimeDependencies(
            catalog_loader=load_catalog,
            speech_delta_publisher=publish_delta,
            speech_sentence_publisher=publish_sentence,
        ),
    )

    assert result["compliance_blocked"] is True
    assert result["violation_sentence"] == "第二句百分百有效。"
    assert result["final_reply"] == COMPLIANCE_FALLBACK
    assert "百分百" not in "".join(deltas)
    assert "".join(sentences) == COMPLIANCE_FALLBACK


@pytest.mark.asyncio
async def test_publish_response_only_publishes_the_post_compliance_text() -> None:
    deltas: list[str] = []
    sentences: list[str] = []

    async def load_catalog(
        _: str, __: bool, ___: dict[str, object]
    ) -> list[dict[str, object]]:
        return []

    async def publish_delta(value: str) -> None:
        deltas.append(value)

    async def publish_sentence(value: str, _sentence_index: int, _sentence_count: int) -> None:
        sentences.append(value)

    violation = await violation_response({"speech_text": "第二句百分百有效。"})
    result = await publish_response(
        violation,
        Runtime(
            context=ShoppingRuntimeDependencies(
                catalog_loader=load_catalog,
                speech_delta_publisher=publish_delta,
                speech_sentence_publisher=publish_sentence,
            )
        ),
    )

    assert result["final_reply"] == COMPLIANCE_FALLBACK
    assert "百分百" not in "".join(deltas)
    assert "".join(sentences) == COMPLIANCE_FALLBACK


@pytest.mark.asyncio
async def test_publish_response_passes_sentence_metadata_to_audio_publisher() -> None:
    metadata: list[tuple[int, int]] = []

    async def load_catalog(
        _: str, __: bool, ___: dict[str, object]
    ) -> list[dict[str, object]]:
        return []

    async def publish_sentence(_: str, sentence_index: int, sentence_count: int) -> None:
        metadata.append((sentence_index, sentence_count))

    result = await publish_response(
        {"speech_text": "第一句安全。第二句安全。"},
        Runtime(
            context=ShoppingRuntimeDependencies(
                catalog_loader=load_catalog,
                speech_sentence_publisher=publish_sentence,
            )
        ),
    )

    assert result["speech_audio_streamed"] is True
    assert metadata == [(1, 2), (2, 2)]


@pytest.mark.asyncio
async def test_compliance_node_checks_replaces_and_publishes_atomically() -> None:
    deltas: list[str] = []
    sentences: list[str] = []

    async def load_catalog(
        _: str, __: bool, ___: dict[str, object]
    ) -> list[dict[str, object]]:
        return []

    async def publish_delta(value: str) -> None:
        deltas.append(value)

    async def publish_sentence(value: str, _sentence_index: int, _sentence_count: int) -> None:
        sentences.append(value)

    result = await compliance_node(
        {"speech_text": "第一句安全。第二句百分百有效。"},
        Runtime(
            context=ShoppingRuntimeDependencies(
                catalog_loader=load_catalog,
                speech_delta_publisher=publish_delta,
                speech_sentence_publisher=publish_sentence,
            )
        ),
    )

    assert result["compliance_blocked"] is True
    assert result["violation_sentence"] == "第二句百分百有效。"
    assert result["final_reply"] == COMPLIANCE_FALLBACK
    assert "百分百" not in "".join(deltas)
    assert "".join(sentences) == COMPLIANCE_FALLBACK


def test_graph_has_one_terminal_compliance_node() -> None:
    node_names = set(build_workflow().get_graph().nodes)

    assert "compliance_node" in node_names
    assert not node_names.intersection(
        {"compliance_check", "violation_response", "publish_response"}
    )


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


@pytest.mark.asyncio
async def test_product_reason_model_uses_one_product_card_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_structured_chat(
        system_prompt: str, payload: dict[str, object], schema: type[object]
    ) -> ProductReason:
        captured["system_prompt"] = system_prompt
        captured["payload"] = payload
        assert schema is ProductReason
        return ProductReason(product_id="product-1", reason="适合你的通勤场景。")

    monkeypatch.setattr(model_module, "_structured_chat", fake_structured_chat)
    result = await model_module.generate_product_reason(
        "推荐通勤耳机",
        {"productId": "product-1", "name": "通勤耳机"},
        "warm-professional",
    )

    assert result == ProductReason(product_id="product-1", reason="适合你的通勤场景。")
    assert "productCard" in str(captured["payload"])
    assert "一张商品卡" in str(captured["system_prompt"])
    assert "不能只用" in str(captured["system_prompt"])


@pytest.mark.asyncio
async def test_recommendation_hook_model_uses_all_product_cards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_structured_chat(
        system_prompt: str, payload: dict[str, object], schema: type[object]
    ) -> RecommendationHook:
        captured["system_prompt"] = system_prompt
        captured["payload"] = payload
        assert schema is RecommendationHook
        return RecommendationHook(
            hook="如果您更在意性价比，推荐您选择第1款（基础台灯）；如果您需要调节色温，推荐您选择第2款（调光台灯）。"
        )

    monkeypatch.setattr(model_module, "_structured_chat", fake_structured_chat)
    hook = await model_module.generate_recommendation_hook(
        "推荐台灯",
        [
            {"productId": "lamp-1", "name": "基础台灯"},
            {"productId": "lamp-2", "name": "调光台灯"},
        ],
        "warm-professional",
        [
            {
                "displayNumber": 1,
                "productId": "lamp-1",
                "name": "基础台灯",
                "condition": "更在意性价比",
            },
            {
                "displayNumber": 2,
                "productId": "lamp-2",
                "name": "调光台灯",
                "condition": "更看重支持调节色温",
            },
        ],
        "",
    )

    assert "第1款（基础台灯）" in hook
    assert "productCards" in str(captured["payload"])
    assert "selectionOptions" in str(captured["payload"])
    assert "选择钩子" in str(captured["system_prompt"])


@pytest.mark.asyncio
async def test_emotional_response_appends_a_fact_based_selection_hook() -> None:
    async def load_catalog(
        _: str, __: bool, ___: dict[str, object]
    ) -> list[dict[str, object]]:
        return []

    result = await emotional_response(
        {
            "model_enabled": False,
            "utterance": "推荐台灯",
            "emotion_style": "warm-professional",
            "product_cards": [
                {
                    "productId": "lamp-1",
                    "name": "基础台灯",
                    "price": 99,
                    "sellingPoints": ["价格亲民"],
                },
                {
                    "productId": "lamp-2",
                    "name": "调光台灯",
                    "price": 159,
                    "sellingPoints": ["支持调节色温"],
                },
            ],
        },
        Runtime(context=ShoppingRuntimeDependencies(catalog_loader=load_catalog)),
    )

    speech = result["speech_text"]
    assert speech.index("第1款（基础台灯）") < speech.index("如果您更在意性价比")
    assert "如果您更在意性价比，推荐您选择第1款（基础台灯）" in speech
    assert "如果您更看重支持调节色温，推荐您选择第2款（调光台灯）" in speech


def test_product_reason_removes_ambiguous_pronouns_and_adds_display_identity() -> None:
    card = {"productId": "headphone-1", "name": "测试头戴式耳机"}

    result = response_module._ensure_reason_identity(
        2,
        card,
        ProductReason(product_id="headphone-1", reason="这款头戴式耳机适合通勤。"),
    )

    assert result.reason == "第2款（测试头戴式耳机）：头戴式耳机适合通勤。"
    assert "这款" not in result.reason


def test_selection_hook_requires_the_correct_displayed_product_name() -> None:
    cards = [
        {
            "productId": "lamp-1",
            "name": "基础台灯",
            "price": 99,
            "sellingPoints": ["价格亲民"],
        },
        {
            "productId": "lamp-2",
            "name": "调光台灯",
            "price": 159,
            "sellingPoints": ["支持调节色温"],
        },
    ]

    assert response_module._is_usable_hook(
        "如果您更在意性价比，推荐您选择第1款（基础台灯）；"
        "如果您更看重支持调节色温，推荐您选择第2款（调光台灯）。",
        cards,
    )
    assert not response_module._is_usable_hook(
        "如果您更在意性价比，推荐您选择第1款（调光台灯）；"
        "如果您更看重支持调节色温，推荐您选择第2款（基础台灯）。",
        cards,
    )


def test_selection_hook_does_not_recommend_shared_headphone_highlight_twice() -> None:
    cards = [
        {
            "productId": "headphone-1",
            "name": "Apple AirPods Max USB-C 头戴耳机",
            "price": 3999,
            "sellingPoints": ["头戴式包裹感", "降噪模式，通勤少些干扰"],
            "attributes": {
                "form": "over-ear",
                "connectivity": "bluetooth",
                "noiseCancellation": True,
            },
        },
        {
            "productId": "headphone-2",
            "name": "Bose QuietComfort 无线降噪耳机",
            "price": 2399,
            "sellingPoints": ["头戴式包裹感", "降噪模式，通勤少些干扰"],
            "attributes": {
                "form": "over-ear",
                "connectivity": "bluetooth",
                "noiseCancellation": True,
                "batteryHours": 24,
            },
        },
        {
            "productId": "headphone-3",
            "name": "Bose QuietComfort Ultra 无线耳机",
            "price": 3199,
            "sellingPoints": ["头戴式包裹感", "降噪模式，通勤少些干扰"],
            "attributes": {
                "form": "over-ear",
                "connectivity": "bluetooth",
                "noiseCancellation": True,
                "batteryHours": 24,
            },
        },
    ]

    hook = response_module._fallback_recommendation_hook(cards)

    assert hook == (
        "如果您更在意性价比，推荐您选择第2款（Bose QuietComfort 无线降噪耳机）；"
        "其余商品的当前资料不足以按不同偏好进一步区分。"
    )
    assert hook.count("头戴式包裹感") == 0


def test_selection_hook_uses_the_longest_numeric_attribute_value() -> None:
    cards = [
        {
            "productId": "headphone-1",
            "name": "续航 20 小时耳机",
            "price": 1999,
            "sellingPoints": ["佩戴舒适"],
            "attributes": {"batteryHours": 20},
        },
        {
            "productId": "headphone-2",
            "name": "续航 40 小时耳机",
            "price": 1999,
            "sellingPoints": ["佩戴舒适"],
            "attributes": {"batteryHours": 40},
        },
    ]

    hook = response_module._fallback_recommendation_hook(cards)

    assert hook == "如果您更在意续航，推荐您选择第2款（续航 40 小时耳机），它的续航可达40小时。"


def test_selection_hook_prefers_the_60_hour_headphone_over_the_45_hour_headphone() -> None:
    cards = [
        {
            "productId": "shure-aonic-50-gen-2",
            "name": "Shure AONIC 50 Gen 2 无线耳机",
            "price": 2999,
            "attributes": {"batteryHours": 45},
        },
        {
            "productId": "sennheiser-momentum-4",
            "name": "Sennheiser MOMENTUM 4 Wireless 头戴耳机",
            "price": 2799,
            "attributes": {"batteryHours": 60},
        },
    ]

    hook = response_module._fallback_recommendation_hook(cards)

    assert "如果您更在意续航，推荐您选择第2款（Sennheiser MOMENTUM 4 Wireless 头戴耳机）" in hook
    assert "它的续航可达60小时" in hook
    assert "更在意续航" in hook
    assert "第1款（Shure AONIC 50 Gen 2 无线耳机）" not in hook.split("如果您更在意续航", 1)[-1]


@pytest.mark.asyncio
async def test_model_hook_with_a_lower_battery_leader_falls_back_to_the_longest_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cards = [
        {
            "productId": "shure-aonic-50-gen-2",
            "name": "Shure AONIC 50 Gen 2 无线耳机",
            "price": 2999,
            "attributes": {"batteryHours": 45},
        },
        {
            "productId": "sennheiser-momentum-4",
            "name": "Sennheiser MOMENTUM 4 Wireless 头戴耳机",
            "price": 2799,
            "attributes": {"batteryHours": 60},
        },
    ]

    async def fake_generate_recommendation_hook(
        *_: object,
    ) -> str:
        return (
            "如果您更在意性价比，推荐您选择第2款（Sennheiser MOMENTUM 4 Wireless 头戴耳机）；"
            "如果您更看重续航时长为45小时，推荐您选择第1款（Shure AONIC 50 Gen 2 无线耳机）。"
        )

    monkeypatch.setattr(
        response_module,
        "generate_recommendation_hook",
        fake_generate_recommendation_hook,
    )

    hook = await response_module._generate_recommendation_hook(
        {"model_enabled": True, "product_cards": cards}
    )

    assert hook == (
        "如果您更在意性价比，推荐您选择第2款（Sennheiser MOMENTUM 4 Wireless 头戴耳机）；"
        "如果您更在意续航，推荐您选择第2款（Sennheiser MOMENTUM 4 Wireless 头戴耳机），"
        "它的续航可达60小时。"
    )


def test_price_leader_requires_a_unique_lowest_price() -> None:
    cards = [
        {"name": "商品一", "price": 99},
        {"name": "商品二", "price": 99},
        {"name": "商品三", "price": 159},
    ]

    assert response_module._price_leader_index(cards) is None


def test_selection_hook_rejects_a_shared_condition_even_with_valid_product_names() -> None:
    cards = [
        {
            "productId": "headphone-1",
            "name": "Apple AirPods Max USB-C 头戴耳机",
            "price": 3999,
            "sellingPoints": ["头戴式包裹感"],
        },
        {
            "productId": "headphone-2",
            "name": "Bose QuietComfort 无线降噪耳机",
            "price": 2399,
            "sellingPoints": ["头戴式包裹感"],
        },
        {
            "productId": "headphone-3",
            "name": "Bose QuietComfort Ultra 无线耳机",
            "price": 3199,
            "sellingPoints": ["头戴式包裹感"],
        },
    ]

    assert not response_module._is_usable_hook(
        "如果您更看重头戴式包裹感，推荐您选择第1款（Apple AirPods Max USB-C 头戴耳机）；"
        "如果您更在意性价比，推荐您选择第2款（Bose QuietComfort 无线降噪耳机）；"
        "如果您更看重头戴式包裹感，推荐您选择第3款（Bose QuietComfort Ultra 无线耳机）。",
        cards,
    )


@pytest.mark.asyncio
async def test_model_selection_hook_falls_back_when_it_reuses_a_shared_condition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cards = [
        {
            "productId": "headphone-1",
            "name": "Apple AirPods Max USB-C 头戴耳机",
            "price": 3999,
            "sellingPoints": ["头戴式包裹感"],
        },
        {
            "productId": "headphone-2",
            "name": "Bose QuietComfort 无线降噪耳机",
            "price": 2399,
            "sellingPoints": ["头戴式包裹感"],
        },
        {
            "productId": "headphone-3",
            "name": "Bose QuietComfort Ultra 无线耳机",
            "price": 3199,
            "sellingPoints": ["头戴式包裹感"],
        },
    ]

    async def fake_generate_recommendation_hook(
        _: str,
        __: list[dict[str, object]],
        ___: str,
        ____: list[dict[str, object]],
        _____: str,
    ) -> str:
        return (
            "如果您更看重头戴式包裹感，推荐您选择第1款（Apple AirPods Max USB-C 头戴耳机）；"
            "如果您更在意性价比，推荐您选择第2款（Bose QuietComfort 无线降噪耳机）；"
            "如果您更看重头戴式包裹感，推荐您选择第3款（Bose QuietComfort Ultra 无线耳机）。"
        )

    monkeypatch.setattr(
        response_module,
        "generate_recommendation_hook",
        fake_generate_recommendation_hook,
    )

    hook = await response_module._generate_recommendation_hook(
        {
            "model_enabled": True,
            "utterance": "推荐一副头戴式蓝牙降噪耳机",
            "product_cards": cards,
        }
    )

    assert hook == (
        "如果您更在意性价比，推荐您选择第2款（Bose QuietComfort 无线降噪耳机）；"
        "其余商品的当前资料不足以按不同偏好进一步区分。"
    )


@pytest.mark.asyncio
async def test_product_reasons_are_generated_concurrently_and_published_by_product(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = 0
    max_active = 0
    started: list[str] = []
    published: list[str] = []

    async def fake_generate_product_reason(
        utterance: str, card: dict[str, object], emotion_style: str
    ) -> ProductReason:
        nonlocal active, max_active
        product_id = str(card["productId"])
        started.append(product_id)
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return ProductReason(product_id=product_id, reason=f"理由-{product_id}")

    async def publish_reason(reason: ProductReason) -> None:
        published.append(reason.product_id)

    async def load_catalog(
        query: str, model_enabled: bool, filters: dict[str, object]
    ) -> list[dict[str, object]]:
        return []

    monkeypatch.setattr(response_module, "generate_product_reason", fake_generate_product_reason)
    cards = [
        {"productId": "product-1", "name": "商品一"},
        {"productId": "product-2", "name": "商品二"},
        {"productId": "product-3", "name": "商品三"},
    ]
    result = await emotional_response(
        {
            "model_enabled": True,
            "utterance": "推荐通勤耳机",
            "emotion_style": "warm-professional",
            "product_cards": cards,
        },
        Runtime(
            context=ShoppingRuntimeDependencies(
                catalog_loader=load_catalog,
                reason_publisher=publish_reason,
            )
        ),
    )

    assert set(started) == {"product-1", "product-2", "product-3"}
    assert max_active == 3
    assert [reason["product_id"] for reason in result["reasons"]] == [
        "product-1",
        "product-2",
        "product-3",
    ]
    assert set(published) == set(started)
    assert result["reasons_streamed"] is True


def test_state_events_does_not_duplicate_streamed_reasons() -> None:
    events = state_events(
        {
            "product_cards": [{"productId": "product-1"}],
            "reasons": [{"product_id": "product-1", "reason": "适合通勤。"}],
            "reasons_streamed": True,
            "final_reply": "已为你筛选。",
        },
        "session-1",
        "turn-1",
    )

    assert not any(event["payload"].get("scope") == "reason" for event in events)
