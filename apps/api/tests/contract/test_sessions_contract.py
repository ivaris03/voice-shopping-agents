import pytest

from voice_shopping_api.modules.sessions import router as sessions_router


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
        headers={"X-User-ID": "00000000-0000-4000-8000-000000000101"},
        json={"reason": "page_closed", "profile": {"city": "上海"}},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "closed", "sessionId": "session-contract"}
    assert captured["profile"] == {"city": "上海"}
    assert captured["close_session"] is True
    assert captured["committed"] is True
