from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from voice_shopping_api.modules.orders import router as orders_router

USER_ID = UUID("00000000-0000-4000-8000-000000000101")
PRODUCT_ID = UUID("20000000-0000-4000-8000-000000000001")
MERCHANT_ID = UUID("10000000-0000-4000-8000-000000000001")
CLIENT_SESSION_ID = UUID("30000000-0000-4000-8000-000000000101")
CLIENT_TURN_ID = UUID("40000000-0000-4000-8000-000000000101")


def _order() -> dict[str, object]:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return {
        "id": UUID("50000000-0000-4000-8000-000000000101"),
        "user_id": USER_ID,
        "merchant_id": MERCHANT_ID,
        "product_id": PRODUCT_ID,
        "status": "pending",
        "quantity": 2,
        "unit_price": Decimal("699.00"),
        "total_amount": Decimal("1398.00"),
        "merchant_snapshot": {"merchantId": str(MERCHANT_ID), "name": "声选 · 通勤音频"},
        "product_snapshot": {
            "productId": str(PRODUCT_ID),
            "name": "Sony WH-CH720N 无线降噪头戴耳机",
        },
        "failure_reason": None,
        "expires_at": now + timedelta(minutes=15),
        "confirmed_at": None,
        "created_at": now,
        "updated_at": now,
    }


@pytest.mark.contract
def test_create_order_contract(client, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    order = _order()

    async def fake_create_pending_order(session, user_id, payload):
        captured["user_id"] = user_id
        captured["payload"] = payload
        return order

    async def fake_commit_or_conflict(session, detail):
        captured["committed"] = True

    monkeypatch.setattr(orders_router, "create_pending_order", fake_create_pending_order)
    monkeypatch.setattr(orders_router, "commit_or_conflict", fake_commit_or_conflict)

    response = client.post(
        "/api/v1/orders",
        headers={"X-User-ID": str(USER_ID)},
        json={
            "productId": str(PRODUCT_ID),
            "quantity": 2,
            "idempotencyKey": "contract-order-001",
            "sessionId": str(CLIENT_SESSION_ID),
            "sourceTurnId": str(CLIENT_TURN_ID),
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == "50000000-0000-4000-8000-000000000101"
    assert body["userId"] == str(USER_ID)
    assert body["productId"] == str(PRODUCT_ID)
    assert body["totalAmount"] == "1398.00"
    assert captured["user_id"] == USER_ID
    assert captured["payload"].idempotency_key == "contract-order-001"
    assert captured["payload"].session_id is None
    assert captured["payload"].source_turn_id is None
    assert captured["committed"] is True


@pytest.mark.contract
def test_create_order_rejects_invalid_body(client, contract_session) -> None:
    response = client.post(
        "/api/v1/orders",
        json={
            "productId": str(PRODUCT_ID),
            "quantity": 0,
            "idempotencyKey": "",
        },
    )

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert contract_session.calls == []
