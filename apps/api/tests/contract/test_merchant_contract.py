from datetime import UTC, datetime
from uuid import UUID

import pytest

from .conftest import FakeResult, ScriptedSession

OWNER_ID = UUID("00000000-0000-4000-8000-000000000002")
MERCHANT_ID = UUID("10000000-0000-4000-8000-000000000001")


@pytest.mark.contract
def test_list_owned_stores_uses_merchant_owner_header(
    client,
    contract_session: ScriptedSession,
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    contract_session.results.append(
        FakeResult(
            [
                {
                    "id": MERCHANT_ID,
                    "owner_user_id": OWNER_ID,
                    "name": "声动数码",
                    "slug": "sound-digital",
                    "description": "音频设备",
                    "logo_url": None,
                    "contact_phone": "13800000002",
                    "is_enabled": True,
                    "disabled_reason": None,
                    "product_count": 3,
                    "created_at": now,
                    "updated_at": now,
                }
            ]
        )
    )

    response = client.get(
        "/api/v1/merchant/stores",
        headers={"X-Merchant-Owner-ID": str(OWNER_ID)},
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["ownerUserId"] == str(OWNER_ID)
    assert response.json()["items"][0]["productCount"] == 3
    assert contract_session.calls[0][1]["owner_id"] == OWNER_ID


@pytest.mark.contract
def test_disabling_a_merchant_requires_a_reason(client, contract_session) -> None:
    response = client.patch(
        f"/api/v1/platform/merchants/{MERCHANT_ID}/status",
        json={"isEnabled": False},
    )

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert contract_session.calls == []
