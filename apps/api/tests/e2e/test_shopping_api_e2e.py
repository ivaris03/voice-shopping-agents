from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text

CUSTOMER_HEADERS = {
    "X-User-ID": "00000000-0000-4000-8000-000000000101",
}


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_e2e_database_runs_migrations_and_demo_seed(e2e_connection) -> None:
    migrations = await e2e_connection.execute(
        text("SELECT version FROM voice_shopping_schema_migrations ORDER BY version")
    )
    assert [row["version"] for row in migrations.mappings()] == [
        "00000000_initial_schema",
        "20260803_restore_default_shopping_contract",
        "20260804_migrate_legacy_catalog_and_profiles",
    ]
    product_count = await e2e_connection.scalar(text("SELECT count(*) FROM products"))
    assert product_count and product_count > 0


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_customer_can_browse_create_and_cancel_order(
    e2e_client: AsyncClient,
    e2e_connection,
) -> None:
    products_response = await e2e_client.get(
        "/api/v1/catalog/products",
        params={"category": "HEADPHONES"},
        headers=CUSTOMER_HEADERS,
    )

    assert products_response.status_code == 200
    products = products_response.json()["items"]
    assert products, "E2E requires products seeded from sql/data.sql"
    product = next((item for item in products if item["stock"] > 0), None)
    assert product is not None, "E2E requires an on-sale product with stock"

    order_payload = {
        "productId": product["id"],
        "quantity": 1,
        "idempotencyKey": f"e2e-{uuid4()}",
        # A stale browser may still include its locally generated IDs. Catalog
        # checkout must not turn either into a foreign-key-backed session link.
        "sessionId": str(uuid4()),
        "sourceTurnId": str(uuid4()),
    }
    create_response = await e2e_client.post(
        "/api/v1/orders",
        headers=CUSTOMER_HEADERS,
        json=order_payload,
    )

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["status"] == "pending"
    assert created["productId"] == product["id"]
    assert created["quantity"] == 1
    persisted = await e2e_connection.execute(
        text("SELECT session_id, source_turn_id FROM orders WHERE id = :id"),
        {"id": created["id"]},
    )
    persisted_row = persisted.mappings().one()
    assert persisted_row["session_id"] is None
    assert persisted_row["source_turn_id"] is None

    retry_response = await e2e_client.post(
        "/api/v1/orders",
        headers=CUSTOMER_HEADERS,
        json=order_payload,
    )
    assert retry_response.status_code == 201
    assert retry_response.json()["id"] == created["id"]

    cancel_response = await e2e_client.post(
        f"/api/v1/orders/{created['id']}/cancel",
        headers=CUSTOMER_HEADERS,
    )
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "fail"
    assert cancel_response.json()["failureReason"] == "user_cancelled"

    orders_response = await e2e_client.get(
        "/api/v1/orders/mine",
        headers=CUSTOMER_HEADERS,
    )
    assert orders_response.status_code == 200
    assert any(order["id"] == created["id"] for order in orders_response.json()["items"])


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_customer_can_confirm_catalog_order_and_decrement_stock(
    e2e_client: AsyncClient,
    e2e_connection,
) -> None:
    products_response = await e2e_client.get(
        "/api/v1/catalog/products",
        params={"category": "HEADPHONES"},
        headers=CUSTOMER_HEADERS,
    )
    product = next(item for item in products_response.json()["items"] if item["stock"] > 1)
    stock_before = await e2e_connection.scalar(
        text("SELECT stock FROM products WHERE id = :id"), {"id": product["id"]}
    )

    create_response = await e2e_client.post(
        "/api/v1/orders",
        headers=CUSTOMER_HEADERS,
        json={
            "productId": product["id"],
            "quantity": 1,
            "idempotencyKey": f"e2e-confirm-{uuid4()}",
        },
    )
    assert create_response.status_code == 201
    order_id = create_response.json()["id"]

    confirm_response = await e2e_client.post(
        f"/api/v1/orders/{order_id}/confirm",
        headers=CUSTOMER_HEADERS,
    )
    assert confirm_response.status_code == 200
    assert confirm_response.json()["status"] == "success"

    persisted = await e2e_connection.execute(
        text("SELECT status, confirmed_at FROM orders WHERE id = :id"), {"id": order_id}
    )
    persisted_order = persisted.mappings().one()
    assert persisted_order["status"] == "success"
    assert persisted_order["confirmed_at"] is not None
    stock_after = await e2e_connection.scalar(
        text("SELECT stock FROM products WHERE id = :id"), {"id": product["id"]}
    )
    assert stock_after == stock_before - 1
