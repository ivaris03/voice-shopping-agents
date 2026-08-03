from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fastapi import HTTPException

from voice_shopping_api.modules.orders import service as order_service
from voice_shopping_api.schemas.domain import OrderCreate

USER_ID = UUID("00000000-0000-4000-8000-000000000101")
OTHER_USER_ID = UUID("00000000-0000-4000-8000-000000000102")
PRODUCT_ID = UUID("20000000-0000-4000-8000-000000000001")
MERCHANT_ID = UUID("10000000-0000-4000-8000-000000000001")
ORDER_ID = UUID("50000000-0000-4000-8000-000000000101")


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []

    def mappings(self) -> "FakeResult":
        return self

    def first(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None


class FakeSession:
    def __init__(self, results: list[FakeResult]) -> None:
        self.results = list(results)
        self.statements: list[str] = []
        self.parameters: list[dict[str, Any]] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> FakeResult:
        self.statements.append(str(statement))
        self.parameters.append(params or {})
        if not self.results:
            raise AssertionError("No scripted result remains")
        return self.results.pop(0)


def _order(*, user_id: UUID = USER_ID, status: str = "pending") -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "id": ORDER_ID,
        "user_id": user_id,
        "merchant_id": MERCHANT_ID,
        "product_id": PRODUCT_ID,
        "status": status,
        "quantity": 2,
        "unit_price": Decimal("699.00"),
        "total_amount": Decimal("1398.00"),
        "merchant_snapshot": {"name": "声动数码"},
        "product_snapshot": {"name": "云雀 Air 降噪耳机"},
        "failure_reason": None,
        "expires_at": now + timedelta(minutes=15),
        "confirmed_at": None,
        "created_at": now,
        "updated_at": now,
    }


def _payload(key: str = "service-order-001") -> OrderCreate:
    return OrderCreate(product_id=PRODUCT_ID, quantity=2, idempotency_key=key)


@pytest.mark.service
@pytest.mark.asyncio
async def test_create_pending_order_reuses_same_user_idempotency_key() -> None:
    existing = _order()
    session = FakeSession([FakeResult([existing])])

    result = await order_service.create_pending_order(session, USER_ID, _payload())

    assert result == existing
    assert len(session.statements) == 1
    assert session.parameters[0]["key"] == "service-order-001"


@pytest.mark.service
@pytest.mark.asyncio
async def test_create_pending_order_returns_not_found_for_unavailable_product() -> None:
    session = FakeSession([FakeResult([]), FakeResult([])])

    with pytest.raises(HTTPException) as exc_info:
        await order_service.create_pending_order(session, USER_ID, _payload())

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "商品不可售或库存不足"
    assert len(session.statements) == 2


@pytest.mark.service
@pytest.mark.asyncio
async def test_confirm_order_decrements_stock_and_updates_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = _order()
    confirmed = _order(status="success")
    confirmed["confirmed_at"] = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
    product = {
        "price": Decimal("699.00"),
        "stock": 10,
        "status": "on_sale",
        "deleted_at": None,
        "is_enabled": True,
        "merchant_deleted_at": None,
    }
    session = FakeSession(
        [
            FakeResult([pending]),
            FakeResult([product]),
            FakeResult(),
            FakeResult([confirmed]),
        ]
    )
    profile_calls: list[tuple[UUID, UUID, str]] = []

    async def fake_update_profiles(session, user_id, product_id, event_type):
        profile_calls.append((user_id, product_id, event_type))

    monkeypatch.setattr(order_service, "update_profiles", fake_update_profiles)

    result = await order_service.confirm_order(session, USER_ID, ORDER_ID)

    assert result["status"] == "success"
    assert session.parameters[2]["quantity"] == 2
    assert session.parameters[2]["product_id"] == PRODUCT_ID
    assert profile_calls == [(USER_ID, PRODUCT_ID, "order")]


@pytest.mark.service
@pytest.mark.asyncio
async def test_cancel_order_only_changes_a_pending_order() -> None:
    cancelled = _order(status="fail")
    cancelled["failure_reason"] = "user_cancelled"
    session = FakeSession([FakeResult([cancelled])])

    result = await order_service.cancel_order(session, USER_ID, ORDER_ID)

    assert result["status"] == "fail"
    assert result["failure_reason"] == "user_cancelled"
    assert len(session.statements) == 1
    assert session.parameters[0]["user_id"] == USER_ID


@pytest.mark.service
@pytest.mark.asyncio
async def test_cancel_order_does_not_reveal_another_users_order() -> None:
    session = FakeSession([FakeResult([]), FakeResult([])])

    with pytest.raises(HTTPException) as exc_info:
        await order_service.cancel_order(session, OTHER_USER_ID, ORDER_ID)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "订单不存在"
