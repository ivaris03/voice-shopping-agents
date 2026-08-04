from datetime import UTC, datetime
from uuid import UUID

import pytest

from .conftest import FakeResult, ScriptedSession


@pytest.mark.contract
def test_list_products_contract(client, contract_session: ScriptedSession) -> None:
    contract_session.results.append(
        FakeResult(
            [
                {
                    "id": UUID("20000000-0000-4000-8000-000000000001"),
                    "merchant_id": UUID("10000000-0000-4000-8000-000000000001"),
                    "merchant_name": "声动数码",
                    "sku": "HEADPHONE-001",
                    "name": "Sony WH-CH720N 无线降噪头戴耳机",
                    "category_l1": "ELECTRONICS",
                    "category_l2": "HEADPHONES",
                    "brand": "Sony",
                    "description": "适合通勤。",
                    "price": "699.00",
                    "stock": 12,
                    "attributes": {"form": "over-ear"},
                    "selling_points": ["主动降噪"],
                    "image_urls": ["https://example.test/headphones.png"],
                    "status": "on_sale",
                    "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                    "updated_at": datetime(2026, 1, 2, tzinfo=UTC),
                }
            ]
        )
    )

    response = client.get(
        "/api/v1/catalog/products",
        params={"category": "HEADPHONES", "query": "Sony"},
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"items"}
    assert body["items"][0]["merchantId"] == "10000000-0000-4000-8000-000000000001"
    assert body["items"][0]["categoryL2"] == "HEADPHONES"
    assert body["items"][0]["sellingPoints"] == ["主动降噪"]
    assert contract_session.calls[0][1]["category"] == "HEADPHONES"
    assert contract_session.calls[0][1]["query"] == "Sony"


@pytest.mark.contract
def test_list_products_rejects_an_invalid_merchant_id(client, contract_session) -> None:
    response = client.get(
        "/api/v1/catalog/products",
        params={"merchantId": "not-a-uuid"},
    )

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert contract_session.calls == []
