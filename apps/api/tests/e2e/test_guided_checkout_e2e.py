from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from voice_shopping_api.agents import service as agent_service
from voice_shopping_api.core.config import get_settings
from voice_shopping_api.core.session import stable_uuid

CUSTOMER_ID = UUID("00000000-0000-4000-8000-000000000101")


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_default_guided_checkout_reaches_confirmed_order(
    e2e_committing_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default UI prompt must reach a real, confirmable order on seeded data."""

    settings = get_settings().model_copy(
        update={"dashscope_api_key": "", "langgraph_checkpoint_enabled": False}
    )

    async def workflow_without_checkpointer():
        return agent_service.shopping_workflow, False

    monkeypatch.setattr(agent_service, "get_settings", lambda: settings)
    monkeypatch.setattr(agent_service, "_workflow_for_turn", workflow_without_checkpointer)
    session_key = f"e2e-guided-{uuid4()}"

    first, _ = await agent_service.process_turn(
        e2e_committing_session,
        session_key,
        "turn-1",
        "我想买一副通勤降噪耳机，预算一千元以内。",
        CUSTOMER_ID,
    )
    assert first["clarification_status"] == "ASK"
    assert {"form", "connectivity"}.issubset(first["missing_slots"])

    second, _ = await agent_service.process_turn(
        e2e_committing_session,
        session_key,
        "turn-2",
        "蓝牙，头戴式。",
        CUSTOMER_ID,
    )
    assert second["clarification_status"] == "READY"
    assert second["product_cards"][0]["name"] == "Sony WH-CH720N 无线降噪头戴耳机"
    product_id = second["product_cards"][0]["productId"]
    persisted_state = await e2e_committing_session.scalar(
        text(
            """
            SELECT business_state
            FROM session_states
            WHERE session_id = :session_id AND turn_id = :turn_id
            """
        ),
        {
            "session_id": stable_uuid(session_key),
            "turn_id": stable_uuid(f"{session_key}:turn-2"),
        },
    )
    assert persisted_state["pending_question"] is None
    stock_before = await e2e_committing_session.scalar(
        text("SELECT stock FROM products WHERE id = :id"), {"id": product_id}
    )

    pending, _ = await agent_service.process_turn(
        e2e_committing_session,
        session_key,
        "turn-3",
        "就买第一款。",
        CUSTOMER_ID,
    )
    assert pending["pending_order"] is not None, pending["final_reply"]
    assert pending["pending_order"]["status"] == "pending"
    order_id = pending["pending_order"]["id"]

    confirmed, _ = await agent_service.process_turn(
        e2e_committing_session,
        session_key,
        "turn-4",
        "确认下单。",
        CUSTOMER_ID,
    )
    assert confirmed["pending_order"] is not None
    assert confirmed["pending_order"]["status"] == "success"

    persisted = await e2e_committing_session.execute(
        text(
            """
            SELECT o.status, o.session_id, o.source_turn_id, s.status AS session_status
            FROM orders AS o
            JOIN sessions AS s ON s.id = o.session_id AND s.user_id = o.user_id
            WHERE o.id = :id
            """
        ),
        {"id": order_id},
    )
    row = persisted.mappings().one()
    assert row["status"] == "success"
    assert row["session_id"] is not None
    assert row["source_turn_id"] is not None
    assert row["session_status"] == "closed"
    stock_after = await e2e_committing_session.scalar(
        text("SELECT stock FROM products WHERE id = :id"), {"id": product_id}
    )
    assert stock_after == stock_before - 1
