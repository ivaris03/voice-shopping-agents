from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from voice_shopping_api.core.database import get_db_session
from voice_shopping_api.main import app


class FakeResult:
    """Small result object for exercising FastAPI routes without a database."""

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

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def scalar_one(self) -> Any:
        if self._has_scalar:
            return self._scalar
        if self._rows:
            return next(iter(self._rows[0].values()))
        raise AssertionError("FakeResult.scalar_one() called without a scalar")

    def scalar_one_or_none(self) -> Any:
        if self._has_scalar:
            return self._scalar
        if self._rows:
            return next(iter(self._rows[0].values()))
        return None


class ScriptedSession:
    def __init__(self, results: list[FakeResult] | None = None) -> None:
        self.results = list(results or [])
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.commit_count = 0
        self.rollback_count = 0

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> FakeResult:
        self.calls.append((str(statement), params or {}))
        if not self.results:
            raise AssertionError(f"No scripted result for SQL: {statement}")
        return self.results.pop(0)

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1


@pytest.fixture
def contract_session() -> ScriptedSession:
    return ScriptedSession()


@pytest.fixture
def client(contract_session: ScriptedSession) -> Any:
    async def override_db_session() -> AsyncIterator[ScriptedSession]:
        yield contract_session

    app.dependency_overrides[get_db_session] = override_db_session
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
