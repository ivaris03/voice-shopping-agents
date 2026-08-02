import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from uuid import UUID, uuid5

from fastapi.encoders import jsonable_encoder
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from voice_shopping_api.agents.checkpointer import get_checkpointer
from voice_shopping_api.agents.graph import build_workflow, shopping_workflow
from voice_shopping_api.agents.model import embed_query
from voice_shopping_api.agents.nodes.response import is_compliant
from voice_shopping_api.agents.state import (
    ShoppingState,
    ShoppingWorkflowContext,
    carry_forward_state,
    state_for_persistence,
)
from voice_shopping_api.core.config import get_settings
from voice_shopping_api.core.queries import PRODUCT_COLUMNS, rows
from voice_shopping_api.core.taxonomy import list_categories
from voice_shopping_api.modules.catalog.profile import profile_snapshot
from voice_shopping_api.modules.orders.service import (
    cancel_order,
    confirm_order,
    create_pending_order,
)
from voice_shopping_api.schemas.domain import OrderCreate

SESSION_NAMESPACE = UUID("f9b9f456-2d14-4ed5-a293-8b4d83f5c777")

_checkpointed_workflow: tuple[object, Any] | None = None
_checkpointed_workflow_lock = asyncio.Lock()


async def _workflow_for_turn() -> Any:
    checkpointer = await get_checkpointer()
    if checkpointer is None:
        return shopping_workflow
    global _checkpointed_workflow
    if _checkpointed_workflow and _checkpointed_workflow[0] is checkpointer:
        return _checkpointed_workflow[1]
    async with _checkpointed_workflow_lock:
        if _checkpointed_workflow and _checkpointed_workflow[0] is checkpointer:
            return _checkpointed_workflow[1]
        workflow = build_workflow(checkpointer=checkpointer)
        _checkpointed_workflow = (checkpointer, workflow)
        return workflow


def stable_uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        return uuid5(SESSION_NAMESPACE, value)


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


async def _load_previous(session: AsyncSession, session_id: UUID) -> ShoppingState:
    result = await session.execute(
        text(
            """
            SELECT workflow_state FROM session_states
            WHERE session_id = :session_id ORDER BY created_at DESC LIMIT 1
            """
        ),
        {"session_id": session_id},
    )
    value = result.scalar_one_or_none()
    return dict(value) if value else {}


async def _catalog(
    session: AsyncSession, utterance: str, model_enabled: bool
) -> list[dict[str, Any]]:
    embedding: str | None = None
    if model_enabled:
        try:
            embedding = json.dumps(await embed_query(utterance))
        except Exception:
            embedding = None
    result = await session.execute(
        text(
            f"""
            SELECT {PRODUCT_COLUMNS},
                CASE WHEN CAST(:embedding AS vector) IS NULL OR p.embedding IS NULL THEN 0
                     ELSE 1 - (p.embedding <=> CAST(:embedding AS vector)) END AS vector_score
            FROM products p JOIN merchants m ON m.id = p.merchant_id
            WHERE p.deleted_at IS NULL AND p.status = 'on_sale' AND p.stock > 0
              AND m.deleted_at IS NULL AND m.is_enabled
            """
        ),
        {"embedding": embedding},
    )
    return rows(result)


async def _taxonomy_context(session: AsyncSession) -> dict[str, Any]:
    categories = await list_categories(session)
    required: dict[str, list[str]] = {}
    allowed: dict[str, list[str]] = {}
    definitions: dict[str, dict[str, Any]] = {}
    definitions_by_category: dict[str, dict[str, dict[str, Any]]] = {}
    questions: dict[str, str] = {}
    names: dict[str, str] = {}
    for category in categories:
        code = str(category["category_l2"])
        names[code] = code
        required[code] = list(category["required_slots"])
        allowed[code] = [*category["required_slots"], *category["optional_slots"]]
        category_definitions: dict[str, dict[str, Any]] = {}
        for slot in category["slots"]:
            key = str(slot["key"])
            definition = {"type": "enum", "values": list(slot["enum_values"])}
            definitions[key] = definition
            category_definitions[key] = definition
            questions[key] = f"请告诉我{key}？"
        definitions_by_category[code] = category_definitions
    return {
        "required_slots_by_category": required,
        "allowed_slots_by_category": allowed,
        "taxonomy_slot_definitions": definitions,
        "taxonomy_slot_definitions_by_category": definitions_by_category,
        "taxonomy_slot_questions": questions,
        "taxonomy_category_names": names,
        "taxonomy_categories": [
            {
                "categoryL1": category["category_l1"],
                "categoryL2": category["category_l2"],
                "requiredSlots": list(category["required_slots"]),
                "optionalSlots": list(category["optional_slots"]),
                "slots": [
                    {
                        "key": slot["key"],
                        "isRequired": slot["is_required"],
                        "enumValues": list(slot["enum_values"]),
                    }
                    for slot in category["slots"]
                ],
            }
            for category in categories
        ],
    }


async def _conversation_history(session: AsyncSession, session_id: UUID) -> list[str]:
    result = await session.execute(
        text(
            """
            SELECT role || ': ' || content AS summary
            FROM session_messages WHERE session_id = :session_id
            ORDER BY created_at DESC, seq DESC LIMIT 6
            """
        ),
        {"session_id": session_id},
    )
    return list(reversed(result.scalars().all()))


def _selected_product_id(utterance: str, cards: list[dict[str, Any]]) -> UUID | None:
    positions = (("第一", 0), ("第二", 1), ("第三", 2))
    for keyword, index in positions:
        if keyword in utterance and len(cards) > index:
            return UUID(str(cards[index]["productId"]))
    for card in cards:
        if card.get("name") and card["name"] in utterance:
            return UUID(str(card["productId"]))
    return UUID(str(cards[0]["productId"])) if len(cards) == 1 else None


async def _latest_pending_order_id(
    session: AsyncSession, user_id: UUID, session_id: UUID
) -> UUID | None:
    result = await session.execute(
        text(
            """
            SELECT id FROM orders
            WHERE user_id = :user_id AND session_id = :session_id AND status = 'pending'
            ORDER BY created_at DESC LIMIT 1
            """
        ),
        {"user_id": user_id, "session_id": session_id},
    )
    return result.scalar_one_or_none()


async def _handle_order(
    session: AsyncSession,
    state: ShoppingState,
    user_id: UUID,
    session_id: UUID,
    turn_id: UUID,
) -> dict[str, Any]:
    intent = state.get("intent") or {}
    action = intent.get("action", "CREATE")
    pending = state.get("pending_order") or {}
    previous_cards = state.get("previous_product_cards", [])
    if action == "CREATE" and not previous_cards:
        reply = "我还没有给你展示推荐商品。请先告诉我想买什么，我会为你筛选合适的商品。"
        return {"speech_text": reply, "final_reply": reply}
    order_id = UUID(str(pending["id"])) if pending.get("id") else None
    order_id = order_id or await _latest_pending_order_id(session, user_id, session_id)
    if action == "CREATE":
        product_id = _selected_product_id(
            state.get("utterance", ""), previous_cards
        )
        if product_id is None:
            reply = "请告诉我要购买推荐结果中的第几款商品。"
            return {"speech_text": reply, "final_reply": reply}
        order = await create_pending_order(
            session,
            user_id,
            OrderCreate(
                product_id=product_id,
                quantity=1,
                idempotency_key=f"voice-{session_id}-{turn_id}",
                session_id=session_id,
                source_turn_id=turn_id,
            ),
        )
        product_name = order["product_snapshot"]["name"]
        total = order["total_amount"]
        reply = (
            f"已生成待确认订单：{product_name}，数量 1，单价 {order['unit_price']} 元，"
            f"合计 {total} 元。订单十五分钟内有效，请说确认下单或取消订单。"
        )
    elif order_id is None:
        reply = "当前没有待确认订单。"
        order = None
    elif action == "CONFIRM":
        order = await confirm_order(session, user_id, order_id)
        reply = (
            "订单已确认，库存已扣减。"
            if order["status"] == "success"
            else f"订单确认失败：{order['failure_reason']}。"
        )
    else:
        order = await cancel_order(session, user_id, order_id)
        reply = "订单已取消。"
    return {
        "pending_order": jsonable_encoder(dict(order)) if order else None,
        "speech_text": reply,
        "final_reply": reply,
        "compliance_blocked": not is_compliant(reply),
    }


async def _persist(
    session: AsyncSession,
    state: ShoppingState,
    user_id: UUID,
    session_id: UUID,
    turn_id: UUID,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO sessions (id, user_id, last_turn_id, last_active_at)
            VALUES (:id, :user_id, :turn_id, CURRENT_TIMESTAMP)
            ON CONFLICT (id) DO UPDATE SET
                last_turn_id = EXCLUDED.last_turn_id,
                last_active_at = EXCLUDED.last_active_at
            """
        ),
        {"id": session_id, "user_id": user_id, "turn_id": turn_id},
    )
    persistable = state_for_persistence(state)
    payload = json.dumps(persistable, ensure_ascii=False, default=_json_default)
    profile = json.dumps(state.get("user_profile_snapshot", {}), ensure_ascii=False)
    pending = state.get("pending_order") or {}
    await session.execute(
        text(
            """
            INSERT INTO session_states (
                session_id, turn_id, workflow_state, user_profile_snapshot, pending_order_id
            ) VALUES (
                :session_id, :turn_id, CAST(:state AS jsonb), CAST(:profile AS jsonb),
                CAST(:pending_order_id AS uuid)
            )
            ON CONFLICT (session_id, turn_id) DO UPDATE SET
                workflow_state = EXCLUDED.workflow_state,
                user_profile_snapshot = EXCLUDED.user_profile_snapshot,
                pending_order_id = EXCLUDED.pending_order_id
            """
        ),
        {
            "session_id": session_id,
            "turn_id": turn_id,
            "state": payload,
            "profile": profile,
            "pending_order_id": pending.get("id"),
        },
    )
    await session.execute(
        text(
            """
            INSERT INTO session_messages (session_id, turn_id, seq, role, message_type, content)
            VALUES
              (:session_id, :turn_id, 0, 'user', 'transcript', :utterance),
              (:session_id, :turn_id, 1, 'assistant', :message_type, :reply)
            ON CONFLICT (session_id, turn_id, seq) DO NOTHING
            """
        ),
        {
            "session_id": session_id,
            "turn_id": turn_id,
            "utterance": state.get("utterance", ""),
            "message_type": "product_cards" if state.get("product_cards") else "text",
            "reply": state.get("final_reply", ""),
        },
    )


def state_events(
    state: ShoppingState,
    session_key: str,
    turn_key: str,
    *,
    start_sequence: int = 1,
    include_processing: bool = True,
    include_cards: bool = True,
) -> list[dict[str, Any]]:
    sequence = start_sequence
    events: list[dict[str, Any]] = []

    def add(event_type: str, payload: dict[str, Any]) -> None:
        nonlocal sequence
        events.append(
            {
                "type": event_type,
                "sessionId": session_key,
                "turnId": turn_key,
                "seq": sequence,
                "payload": payload,
            }
        )
        sequence += 1

    if include_processing:
        add("flow.status", {"status": "processing", "intent": state.get("intent")})
    if include_cards and state.get("product_cards"):
        add(
            "recommendation.cards",
            {
                "productCards": state["product_cards"],
                "emotionStyle": state.get("emotion_style"),
            },
        )
    for reason in state.get("reasons", []):
        if is_compliant(reason["reason"]):
            add(
                "text.delta",
                {
                    "scope": "reason",
                    "productId": reason["product_id"],
                    "delta": reason["reason"],
                },
            )
    reply = state.get("final_reply", "")
    if not state.get("speech_streamed"):
        for start in range(0, len(reply), 12):
            delta = reply[start : start + 12]
            if is_compliant(delta):
                add("text.delta", {"scope": "speech", "delta": delta})
    add(
        "text.completed",
        {"text": reply, "complianceBlocked": state.get("compliance_blocked", False)},
    )
    if state.get("pending_order"):
        add("order.updated", {"order": state["pending_order"]})
    add("flow.status", {"status": "completed"})
    return events


async def process_turn(
    session: AsyncSession,
    session_key: str,
    turn_key: str,
    utterance: str,
    user_id: UUID,
    on_events: Callable[[list[dict[str, Any]]], Awaitable[None]] | None = None,
) -> tuple[ShoppingState, list[dict[str, Any]]]:
    settings = get_settings()
    session_id = stable_uuid(session_key)
    turn_id = stable_uuid(f"{session_key}:{turn_key}")
    previous = await _load_previous(session, session_id)
    carried_forward = carry_forward_state(previous)
    model_enabled = bool(settings.dashscope_api_key)
    taxonomy_context = await _taxonomy_context(session)
    state_input: ShoppingState = {
        **carried_forward,
        **taxonomy_context,
        "session_id": session_key,
        "turn_id": turn_key,
        "user_id": str(user_id),
        "utterance": utterance.strip(),
        "conversation_history": await _conversation_history(session, session_id),
        "model_enabled": model_enabled,
        "intent": {},
        "catalog_products": [],
        "user_profile_snapshot": await profile_snapshot(session, user_id),
        "previous_product_cards": carried_forward.get("product_cards", []),
        "product_cards": [],
        "reasons": [],
        "speech_text": "",
        "final_reply": "",
        "speech_streamed": False,
        "compliance_blocked": False,
    }
    run_config = {
        "run_name": "voice-shopping-turn",
        "configurable": {"thread_id": session_key},
        "tags": [settings.environment, f"model:{settings.agent_model}"],
        "metadata": {
            "thread_id": session_key,
            "turn_id": turn_key,
            "environment": settings.environment,
            "agent_model": settings.agent_model,
            "embedding_model": settings.embedding_model,
            "reranker_model": settings.reranker_model,
        },
    }
    result: ShoppingState = dict(state_input)
    next_sequence = 1
    if on_events:
        await on_events(
            [
                {
                    "type": "flow.status",
                    "sessionId": session_key,
                    "turnId": turn_key,
                    "seq": next_sequence,
                    "payload": {"status": "processing"},
                }
            ]
        )
        next_sequence += 1

    async def publish_speech_delta(delta: str) -> None:
        nonlocal next_sequence
        if not on_events or not delta or not is_compliant(delta):
            return
        await on_events(
            [
                {
                    "type": "text.delta",
                    "sessionId": session_key,
                    "turnId": turn_key,
                    "seq": next_sequence,
                    "payload": {"scope": "speech", "delta": delta},
                }
            ]
        )
        next_sequence += 1

    workflow = await _workflow_for_turn()
    async for update in workflow.astream(
        state_input,
        config=run_config,
        context=ShoppingWorkflowContext(
            catalog_loader=lambda query, enabled: _catalog(session, query, enabled),
            order_handler=lambda current_state: _handle_order(
                session, current_state, user_id, session_id, turn_id
            ),
            speech_delta_publisher=publish_speech_delta if on_events else None,
        ),
        stream_mode="updates",
    ):
        for node_name, partial in update.items():
            result.update(partial)
            if on_events and node_name == "recommendation_agent" and result.get("product_cards"):
                await on_events(
                    [
                        {
                            "type": "recommendation.cards",
                            "sessionId": session_key,
                            "turnId": turn_key,
                            "seq": next_sequence,
                            "payload": {
                                "productCards": result["product_cards"],
                                "emotionStyle": result.get("emotion_style"),
                            },
                        }
                    ]
                )
                next_sequence += 1
    await _persist(session, result, user_id, session_id, turn_id)
    await session.commit()
    events = state_events(
        result,
        session_key,
        turn_key,
        start_sequence=next_sequence,
        include_processing=not bool(on_events),
        include_cards=not bool(on_events),
    )
    if on_events:
        await on_events(events)
        return result, []
    return result, events
