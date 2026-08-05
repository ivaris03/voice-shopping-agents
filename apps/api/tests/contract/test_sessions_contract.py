from uuid import UUID

import pytest

from voice_shopping_api.core.identity import Principal, create_access_token
from voice_shopping_api.modules.sessions import router as sessions_router


def _customer_headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer "
        + create_access_token(
            Principal(
                user_id=UUID("00000000-0000-4000-8000-000000000101"),
                email="lin@example.com",
                display_name="小林",
                role="customer",
            )
        )[0]
    }


@pytest.mark.contract
def test_close_session_contract(client, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_finalize_session_profile(
        session,
        session_id,
        user_id,
        profile=None,
        *,
        close_session=False,
    ):
        captured.update(
            session_id=session_id,
            user_id=user_id,
            profile=profile,
            close_session=close_session,
        )
        return {"status": "closed", "sessionId": "session-contract"}

    async def fake_commit_or_conflict(session, detail):
        captured["committed"] = True

    monkeypatch.setattr(
        sessions_router,
        "finalize_session_profile",
        fake_finalize_session_profile,
    )
    monkeypatch.setattr(sessions_router, "commit_or_conflict", fake_commit_or_conflict)

    response = client.post(
        "/api/v1/sessions/session-contract/close",
        headers=_customer_headers(),
        json={"reason": "page_closed", "profile": {"city": "上海"}},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "closed", "sessionId": "session-contract"}
    assert captured["profile"] == {"city": "上海"}
    assert captured["close_session"] is True
    assert captured["committed"] is True


@pytest.mark.contract
@pytest.mark.parametrize("field", ["budget", "budgetBand", "budget_band"])
def test_close_session_rejects_budget_profile_fields(client, field: str) -> None:
    response = client.post(
        "/api/v1/sessions/session-contract/close",
        headers=_customer_headers(),
        json={"profile": {field: 2000}},
    )

    assert response.status_code == 422
    assert "budgetMax" in response.text
