from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from voice_shopping_api.agents import service as agent_service
from voice_shopping_api.core.config import get_settings

ACTIVE_SESSION = "30000000-0000-4000-8000-000000000001"
CLOSED_SESSION = "30000000-0000-4000-8000-000000000002"
OTHER_CUSTOMER_ID = UUID("00000000-0000-4000-8000-000000000102")


def _offline_settings():
    return get_settings().model_copy(
        update={"dashscope_api_key": "", "langgraph_checkpoint_enabled": False}
    )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_session_turn_rejects_a_foreign_user(
    e2e_committing_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_service, "get_settings", _offline_settings)

    with pytest.raises(HTTPException) as error:
        await agent_service.process_turn(
            e2e_committing_session,
            ACTIVE_SESSION,
            "foreign-turn",
            "读取这个会话",
            OTHER_CUSTOMER_ID,
        )

    assert error.value.status_code == 404
    await e2e_committing_session.rollback()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_closed_session_cannot_accept_a_new_turn(
    e2e_committing_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_service, "get_settings", _offline_settings)

    with pytest.raises(HTTPException) as error:
        await agent_service.process_turn(
            e2e_committing_session,
            CLOSED_SESSION,
            "closed-turn",
            "继续下单",
            OTHER_CUSTOMER_ID,
        )

    assert error.value.status_code == 409
    await e2e_committing_session.rollback()
