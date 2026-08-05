from datetime import UTC, datetime
from uuid import UUID

import pytest

from voice_shopping_api.core.identity import Principal, create_access_token

from .conftest import FakeResult, ScriptedSession

OWNER_ID = UUID("00000000-0000-4000-8000-000000000002")
MERCHANT_ID = UUID("10000000-0000-4000-8000-000000000001")


def _merchant_headers() -> dict[str, str]:
    token, _ = create_access_token(
        Principal(
            user_id=OWNER_ID,
            email="audio@example.com",
            display_name="声选音频商家",
            role="merchant",
        )
    )
    return {"Authorization": f"Bearer {token}"}


def _platform_headers() -> dict[str, str]:
    token, _ = create_access_token(
        Principal(
            user_id=UUID("00000000-0000-4000-8000-000000000001"),
            email="admin@example.com",
            display_name="平台管理员",
            role="platform",
        )
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.contract
def test_list_owned_stores_uses_the_authenticated_merchant(
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
        headers=_merchant_headers(),
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["ownerUserId"] == str(OWNER_ID)
    assert response.json()["items"][0]["productCount"] == 3
    assert contract_session.calls[0][1]["owner_id"] == OWNER_ID


@pytest.mark.contract
def test_disabling_a_merchant_requires_a_reason(client, contract_session) -> None:
    response = client.patch(
        f"/api/v1/platform/merchants/{MERCHANT_ID}/status",
        headers=_platform_headers(),
        json={"isEnabled": False},
    )

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert contract_session.calls == []


@pytest.mark.contract
def test_customer_cannot_access_merchant_or_platform_routes(client) -> None:
    customer_token, _ = create_access_token(
        Principal(
            user_id=UUID("00000000-0000-4000-8000-000000000101"),
            email="lin@example.com",
            display_name="小林",
            role="customer",
        )
    )
    headers = {"Authorization": f"Bearer {customer_token}"}

    merchant_response = client.get("/api/v1/merchant/stores", headers=headers)
    platform_response = client.get("/api/v1/platform/merchants", headers=headers)

    assert merchant_response.status_code == 403
    assert platform_response.status_code == 403
