import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from voice_shopping_api.agents.checkpointer import get_checkpointer
from voice_shopping_api.agents.graph import build_workflow, shopping_workflow
from voice_shopping_api.agents.model import embed_query
from voice_shopping_api.agents.nodes.response import is_compliant
from voice_shopping_api.agents.state import (
    BUSINESS_STATE_VERSION,
    CatalogFilters,
    ProductReason,
    ShoppingInputState,
    ShoppingOutputState,
    ShoppingRuntimeDependencies,
    ShoppingState,
    carry_forward_state,
    state_for_output,
    state_for_persistence,
)
from voice_shopping_api.core.config import get_settings
from voice_shopping_api.core.queries import PRODUCT_COLUMNS, rows
from voice_shopping_api.core.session import stable_uuid
from voice_shopping_api.core.taxonomy import list_categories
from voice_shopping_api.modules.catalog.profile import (
    extract_static_profile_candidates,
    merge_static_profile_patches,
    profile_snapshot,
)
from voice_shopping_api.modules.orders.service import (
    cancel_order,
    confirm_order,
    create_pending_order,
)
from voice_shopping_api.modules.sessions.service import finalize_session_profile
from voice_shopping_api.schemas.domain import OrderCreate

_checkpointed_workflow: tuple[object, Any] | None = None
_checkpointed_workflow_lock = asyncio.Lock()


async def _workflow_for_turn() -> tuple[Any, bool]:
    checkpointer = await get_checkpointer()
    if checkpointer is None:
        return shopping_workflow, False
    global _checkpointed_workflow
    if _checkpointed_workflow and _checkpointed_workflow[0] is checkpointer:
        return _checkpointed_workflow[1], True
    async with _checkpointed_workflow_lock:
        if _checkpointed_workflow and _checkpointed_workflow[0] is checkpointer:
            return _checkpointed_workflow[1], True
        workflow = build_workflow(checkpointer=checkpointer)
        _checkpointed_workflow = (checkpointer, workflow)
        return workflow, True


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


async def _load_business_state(session: AsyncSession, session_id: UUID) -> ShoppingState:
    result = await session.execute(
        text(
            """
            SELECT business_state FROM session_states
            WHERE session_id = :session_id ORDER BY created_at DESC LIMIT 1
            """
        ),
        {"session_id": session_id},
    )
    value = result.scalar_one_or_none()
    return dict(value) if value else {}


# 数值属性槽位：商品侧取值 >= 槽位要求即命中（键缺失的行被 NULL 排除）。
NUMERIC_SLOTS = frozenset({"batteryHours", "pressureBar", "waterTankMl", "capacityL"})


def _attribute_condition(slot: str, value: object, params: dict[str, str]) -> str:
    """按槽位键把 attributes(JSONB) 过滤拼成一条 SQL 条件。

    键来自平台 taxonomy（可信），内联进 SQL；值来自槽位抽取，一律参数化。
    键缺失时 JSONB 表达式求值为 NULL，WHERE 把该行排除——与旧的 Python 侧
    _attribute_matches 语义一致。
    """
    param = f"slots_{slot}"
    if slot == "gender":
        params[param] = str(value)
        return f"(p.attributes->>'gender' = 'unisex' OR p.attributes->>'gender' = :{param})"
    if slot == "size":
        # 与 _product_attribute_value 一致：优先 size 键（2 元素数组按区间，
        # 其他数组按包含，标量按等值），缺失时回退 sizeRange 区间。
        # 等值分支用文本参数，区间分支用独立数值参数：Postgres 会把出现在
        # CAST(... AS float8) / float8 比较里的参数定型为 float8，asyncpg
        # 因而要求 Python 数字，不能与 text 等值共用同一个参数。
        params[param] = str(value)
        params[f"{param}_num"] = float(value)
        params[f"{param}_json"] = json.dumps(str(value))
        return f"""
            (
                (p.attributes ? 'size' AND jsonb_typeof(p.attributes->'size') = 'array'
                 AND jsonb_array_length(p.attributes->'size') = 2
                 AND (p.attributes->'size'->>0)::float8 <= :{param}_num
                 AND (p.attributes->'size'->>1)::float8 >= :{param}_num)
                OR (p.attributes ? 'size' AND jsonb_typeof(p.attributes->'size') = 'array'
                 AND p.attributes->'size' @> CAST(:{param}_json AS jsonb))
                OR (p.attributes ? 'size' AND jsonb_typeof(p.attributes->'size') <> 'array'
                 AND p.attributes->>'size' = :{param})
                OR (NOT p.attributes ? 'size' AND p.attributes ? 'sizeRange'
                 AND (p.attributes->'sizeRange'->>0)::float8 <= :{param}_num
                 AND (p.attributes->'sizeRange'->>1)::float8 >= :{param}_num)
            )
        """
    if slot == "waterResistance":
        # 商品值可能是 "100m" 或 "IPX5"，只取数字部分比较；无数字 → NULL → 排除。
        params[param] = float(value)
        return (
            "NULLIF(regexp_replace(p.attributes->>'waterResistance', '[^0-9]', '', 'g'), '')"
            f"::float8 >= :{param}"
        )
    if slot in NUMERIC_SLOTS:
        params[param] = float(value)
        return f"(p.attributes->>'{slot}')::float8 >= :{param}"
    # 默认：枚举标量等值、列表包含统一用 @>（可命中 attributes GIN 索引）。
    # 布尔槽位必须序列化为 JSON true/false，而非 str() 后的 "True"/"False"
    # 字符串，否则与属性中的 JSON 布尔做包含比较永远不命中。
    params[f"{param}_json"] = json.dumps(value)
    return f"p.attributes->'{slot}' @> CAST(:{param}_json AS jsonb)"


def _build_catalog_query(
    filters: CatalogFilters, *, embedding: str | None
) -> tuple[str, dict[str, str | None]]:
    """按已填槽位动态拼一条召回 SQL：过滤 + 排序 + LIMIT 20。

    向量可用时走 HNSW 形态：ORDER BY 原始余弦距离表达式，并显式补
    p.embedding IS NOT NULL 以证明部分索引谓词；embedding 为 None 时降级为
    确定性的 created_at 排序（embedding 缺失的商品仍参与）。
    """
    conditions = [
        "p.deleted_at IS NULL",
        "p.status = 'on_sale'",
        "p.stock > 0",
        "m.deleted_at IS NULL",
        "m.is_enabled",
    ]
    params: dict[str, str | None] = {}
    category = filters.get("category")
    if category:
        params["category"] = category
        conditions.append("p.category_l2 = :category")
    slots = filters.get("slots", {})
    budget = slots.get("budgetMax")
    if budget is not None:
        params["budget"] = str(budget)
        conditions.append("p.price <= CAST(:budget AS numeric)")
    # 已填槽位（含可选槽位）全部参与硬过滤。澄清节点保证槽位键来自品类
    # 允许集合（required ∪ optional + budgetMax）且值已校验，可直接拼条件；
    # 未填的槽位值为 None，自然跳过。
    for slot, value in slots.items():
        if slot == "budgetMax" or value is None:
            continue
        conditions.append(_attribute_condition(slot, value, params))
    if embedding is not None:
        params["embedding"] = embedding
        conditions.append("p.embedding IS NOT NULL")
        order_by = "p.embedding <=> CAST(:embedding AS vector)"
    else:
        params["embedding"] = None
        order_by = "p.created_at DESC"
    return (
        f"""
            SELECT {PRODUCT_COLUMNS},
                CASE WHEN CAST(:embedding AS vector) IS NULL OR p.embedding IS NULL THEN 0
                     ELSE 1 - (p.embedding <=> CAST(:embedding AS vector)) END AS vector_score
            FROM products p JOIN merchants m ON m.id = p.merchant_id
            WHERE {" AND ".join(conditions)}
            ORDER BY {order_by}
            LIMIT 20
        """,
        params,
    )


async def _catalog(
    session: AsyncSession,
    utterance: str,
    model_enabled: bool,
    filters: CatalogFilters,
) -> list[dict[str, Any]]:
    embedding: str | None = None
    if model_enabled:
        try:
            embedding = json.dumps(await embed_query(utterance))
        except Exception:
            embedding = None
    sql, params = _build_catalog_query(filters, embedding=embedding)
    result = await session.execute(text(sql), params)
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
    pending = state.get("pending_order") or {}
    pending_order_id = (
        pending.get("id") if pending.get("status", "pending") == "pending" else None
    )
    await session.execute(
        text(
            """
            INSERT INTO session_states (
                session_id, turn_id, state_version, business_state, pending_order_id
            ) VALUES (
                :session_id, :turn_id, :state_version, CAST(:state AS jsonb),
                CAST(:pending_order_id AS uuid)
            )
            ON CONFLICT (session_id, turn_id) DO UPDATE SET
                state_version = EXCLUDED.state_version,
                business_state = EXCLUDED.business_state,
                pending_order_id = EXCLUDED.pending_order_id
            """
        ),
        {
            "session_id": session_id,
            "turn_id": turn_id,
            "state_version": BUSINESS_STATE_VERSION,
            "state": payload,
            "pending_order_id": pending_order_id,
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
    if not state.get("reasons_streamed"):
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


# Keep the user-facing wording next to the workflow node names so a renamed
# graph node cannot silently fall back to a generic "Agent processing" label.
WORKFLOW_NODE_LABELS: dict[str, str] = {
    "intent_agent": "意图识别 Agent 运行中",
    "clarification_agent": "需求澄清 Agent 运行中",
    "recommendation_agent": "商品召回与推荐 Agent 运行中",
    "order_node": "订单处理节点运行中",
    "emotional_agent": "回复 Agent 运行中",
    "compliance_check": "合规检查节点运行中",
    "violation_response": "违规回复节点运行中",
    "publish_response": "安全回复发布中",
}


async def process_turn(
    session: AsyncSession,
    session_key: str,
    turn_key: str,
    utterance: str,
    user_id: UUID,
    on_events: Callable[[list[dict[str, Any]]], Awaitable[None]] | None = None,
    on_speech_sentence: Callable[[str], Awaitable[None]] | None = None,
    profile_updates: Mapping[str, Any] | None = None,
) -> tuple[ShoppingOutputState, list[dict[str, Any]]]:
    settings = get_settings()
    session_id = stable_uuid(session_key)
    turn_id = stable_uuid(f"{session_key}:{turn_key}")
    workflow, checkpoint_enabled = await _workflow_for_turn()
    run_config = {
        "run_name": "voice-shopping-turn",
        "configurable": {"thread_id": str(session_id)},
        "tags": [settings.environment, f"model:{settings.agent_model}"],
        "metadata": {
            "thread_id": str(session_id),
            "turn_id": turn_key,
            "environment": settings.environment,
            "agent_model": settings.agent_model,
            "embedding_model": settings.embedding_model,
            "reranker_model": settings.reranker_model,
        },
    }
    previous: ShoppingState = {}
    if checkpoint_enabled:
        checkpoint = await workflow.aget_state(run_config)
        if isinstance(checkpoint.values, Mapping):
            previous = dict(checkpoint.values)
    if not previous:
        previous = await _load_business_state(session, session_id)
    carried_forward = carry_forward_state(previous)
    pending_order_id = await _latest_pending_order_id(session, user_id, session_id)
    pending_order = (
        {"id": str(pending_order_id), "status": "pending"}
        if pending_order_id
        else None
    )
    model_enabled = bool(settings.dashscope_api_key)
    taxonomy_context = await _taxonomy_context(session)
    profile_candidates = merge_static_profile_patches(
        carried_forward.get("user_profile_updates", {}),
        extract_static_profile_candidates(utterance, carried_forward.get("slots", {})),
        profile_updates,
    )
    state_input: ShoppingInputState = {
        **carried_forward,
        **taxonomy_context,
        "session_id": session_key,
        "turn_id": turn_key,
        "user_id": str(user_id),
        "utterance": utterance.strip(),
        "conversation_history": await _conversation_history(session, session_id),
        "model_enabled": model_enabled,
        "catalog_products": [],
        "user_profile_snapshot": await profile_snapshot(session, user_id),
        "user_profile_updates": profile_candidates,
        "previous_product_cards": carried_forward.get("product_cards", []),
        "product_cards": [],
        "pending_order": pending_order,
    }
    result: ShoppingState = dict(state_input)
    next_sequence = 1
    event_lock = asyncio.Lock()

    async def publish_event(event_type: str, payload: dict[str, Any]) -> None:
        nonlocal next_sequence
        if not on_events:
            return
        async with event_lock:
            event = {
                "type": event_type,
                "sessionId": session_key,
                "turnId": turn_key,
                "seq": next_sequence,
                "payload": payload,
            }
            next_sequence += 1
            await on_events([event])

    if on_events:
        await publish_event("flow.status", {"status": "processing"})

    async def publish_speech_delta(delta: str) -> None:
        if not on_events or not delta:
            return
        await publish_event("text.delta", {"scope": "speech", "delta": delta})

    async def publish_reason(reason: ProductReason) -> None:
        if not on_events or not is_compliant(reason.reason):
            return
        await publish_event(
            "text.delta",
            {
                "scope": "reason",
                "productId": reason.product_id,
                "delta": reason.reason,
            },
        )

    async for stream_item in workflow.astream(
        state_input,
        config=run_config,
        context=ShoppingRuntimeDependencies(
            catalog_loader=lambda query, enabled, filters: _catalog(
                session, query, enabled, filters
            ),
            order_handler=lambda current_state: _handle_order(
                session, current_state, user_id, session_id, turn_id
            ),
            reason_publisher=publish_reason if on_events else None,
            speech_delta_publisher=publish_speech_delta if on_events else None,
            speech_sentence_publisher=on_speech_sentence if on_events else None,
        ),
        # ``tasks`` emits a start item before each node executes.  That is the
        # point at which the UI can truthfully show the node as "running";
        # updates alone arrive only after the node has finished.
        stream_mode=["updates", "tasks"],
    ):
        stream_mode, payload = stream_item
        if stream_mode == "tasks":
            # Task start payloads have ``input``; task result payloads have
            # ``result``/``error``.  Only publish starts to avoid flickering
            # through a completed state between adjacent workflow nodes.
            if on_events and isinstance(payload, dict) and "input" in payload:
                node_name = str(payload.get("name", ""))
                label = WORKFLOW_NODE_LABELS.get(node_name)
                if label:
                    await publish_event(
                        "flow.status",
                        {
                            "status": "processing",
                            "node": node_name,
                            "label": label,
                        },
                    )
            continue
        if stream_mode != "updates" or not isinstance(payload, dict):
            continue
        for node_name, partial in payload.items():
            if not isinstance(partial, dict):
                continue
            result.update(partial)
            if on_events and node_name == "recommendation_agent" and result.get("product_cards"):
                await publish_event(
                    "recommendation.cards",
                    {
                        "productCards": result["product_cards"],
                        "emotionStyle": result.get("emotion_style"),
                    },
                )
    result["user_profile_updates"] = merge_static_profile_patches(
        state_input.get("user_profile_updates", {}),
        extract_static_profile_candidates(
            result.get("utterance", ""), result.get("slots", {})
        ),
    )
    await _persist(session, result, user_id, session_id, turn_id)
    terminal_order = (result.get("pending_order") or {}).get("status") in {"success", "fail"}
    if terminal_order:
        await finalize_session_profile(
            session,
            session_id,
            user_id,
            result.get("user_profile_updates"),
            close_session=True,
        )
        result["user_profile_snapshot"] = await profile_snapshot(session, user_id)
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
        return state_for_output(result), []
    return state_for_output(result), events
