from typing import Any
from uuid import UUID

import pytest
from fastapi import HTTPException

from voice_shopping_api.modules.sessions import service as sessions_service

USER_ID = UUID("00000000-0000-4000-8000-000000000101")
OTHER_USER_ID = UUID("00000000-0000-4000-8000-000000000102")
SESSION_ID = UUID("30000000-0000-4000-8000-000000000101")


class FakeResult:
    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        *,
        scalar: Any = None,
        has_scalar: bool = False,
    ) -> None:
        self._rows = rows or []
        self._scalar = scalar
        self._has_scalar = has_scalar

    def mappings(self) -> "FakeResult":
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self) -> Any:
        if self._has_scalar:
            return self._scalar
        if self._rows:
            return next(iter(self._rows[0].values()))
        return None


class FakeSession:
    def __init__(self, results: list[FakeResult]) -> None:
        self.results = list(results)
        self.statements: list[str] = []
        self.parameters: list[dict[str, Any]] = []

    async def execute(
        self, statement: Any, params: dict[str, Any] | None = None
    ) -> FakeResult:
        self.statements.append(str(statement))
        self.parameters.append(params or {})
        if not self.results:
            raise AssertionError("No scripted result remains")
        return self.results.pop(0)


def _session_row(*, user_id: UUID = USER_ID, status: str = "active") -> dict[str, Any]:
    return {"id": SESSION_ID, "user_id": user_id, "status": status}


@pytest.mark.service
@pytest.mark.asyncio
async def test_ensure_active_session_creates_and_locks_a_new_owned_session() -> None:
    session = FakeSession(
        [
            FakeResult(scalar=SESSION_ID, has_scalar=True),
            FakeResult([_session_row()]),
        ]
    )

    result = await sessions_service.ensure_active_session(session, SESSION_ID, USER_ID)

    assert result["id"] == SESSION_ID
    assert result["_created"] is True
    assert "ON CONFLICT (id) DO NOTHING" in session.statements[0]
    assert "FOR UPDATE" in session.statements[1]


@pytest.mark.service
@pytest.mark.asyncio
async def test_ensure_active_session_hides_foreign_session_and_rejects_closed_session() -> None:
    foreign = FakeSession(
        [
            FakeResult(scalar=None, has_scalar=True),
            FakeResult([_session_row(user_id=OTHER_USER_ID)]),
        ]
    )
    with pytest.raises(HTTPException) as foreign_error:
        await sessions_service.ensure_active_session(foreign, SESSION_ID, USER_ID)
    assert foreign_error.value.status_code == 404
    assert foreign_error.value.detail == sessions_service.SESSION_NOT_FOUND_DETAIL

    closed = FakeSession(
        [
            FakeResult(scalar=None, has_scalar=True),
            FakeResult([_session_row(status="closed")]),
        ]
    )
    with pytest.raises(HTTPException) as closed_error:
        await sessions_service.ensure_active_session(closed, SESSION_ID, USER_ID)
    assert closed_error.value.status_code == 409
    assert closed_error.value.detail == sessions_service.SESSION_CLOSED_DETAIL


@pytest.mark.service
@pytest.mark.asyncio
async def test_close_of_unknown_session_is_not_treated_as_a_profile_update() -> None:
    session = FakeSession([FakeResult()])

    with pytest.raises(HTTPException) as error:
        await sessions_service.finalize_session_profile(
            session,
            SESSION_ID,
            USER_ID,
            {"city": "上海"},
            close_session=True,
        )

    assert error.value.status_code == 404
    assert len(session.statements) == 1


@pytest.mark.service
@pytest.mark.asyncio
async def test_repeated_close_does_not_reopen_a_closed_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_calls: list[dict[str, Any] | None] = []

    async def fake_update_static_profile(session, user_id, patch):
        profile_calls.append(patch)
        return ["city", "height_cm", "budget_band"]

    monkeypatch.setattr(sessions_service, "update_static_profile", fake_update_static_profile)
    session = FakeSession([FakeResult([_session_row(status="closed")])])

    result = await sessions_service.finalize_session_profile(
        session,
        SESSION_ID,
        USER_ID,
        {"city": "上海"},
        close_session=True,
    )

    assert result == {
        "sessionId": str(SESSION_ID),
        "status": "closed",
        "updatedFields": ["city", "heightCm", "budgetBand"],
    }
    assert profile_calls == [{"city": "上海"}]
    assert len(session.statements) == 1


@pytest.mark.service
@pytest.mark.asyncio
async def test_active_close_returns_camel_case_updated_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_update_static_profile(session, user_id, patch):
        return ["height_cm", "weight_kg", "skin_type", "tech_savvy", "budget_band"]

    monkeypatch.setattr(sessions_service, "update_static_profile", fake_update_static_profile)
    session = FakeSession(
        [
            FakeResult([_session_row()]),
            FakeResult(scalar={}, has_scalar=True),
            FakeResult(),
        ]
    )

    result = await sessions_service.finalize_session_profile(
        session,
        SESSION_ID,
        USER_ID,
        {"heightCm": 170},
        close_session=True,
    )

    assert result == {
        "sessionId": str(SESSION_ID),
        "status": "closed",
        "updatedFields": ["heightCm", "weightKg", "skinType", "techSavvy", "budgetBand"],
    }
