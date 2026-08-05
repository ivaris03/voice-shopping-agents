from uuid import UUID

import pytest

from voice_shopping_api.core.identity import principal_from_access_token

from .conftest import FakeResult, ScriptedSession

USER_ID = UUID("00000000-0000-4000-8000-000000000101")


@pytest.mark.contract
def test_login_verifies_the_existing_phone_and_password_hash_and_issues_a_jwt(
    client,
    contract_session: ScriptedSession,
) -> None:
    contract_session.results.append(
        FakeResult(
            [
                {
                    "id": USER_ID,
                    "email": "lin@example.com",
                    "display_name": "小林",
                    "role": "customer",
                }
            ]
        )
    )

    response = client.post(
        "/api/v1/auth/login",
        json={"phone": " 139 0000 0101 ", "password": "12345678"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["tokenType"] == "bearer"
    assert body["expiresIn"] > 0
    assert body["user"] == {
        "id": str(USER_ID),
        "email": "lin@example.com",
        "displayName": "小林",
        "role": "customer",
    }
    principal = principal_from_access_token(body["accessToken"])
    assert principal.user_id == USER_ID
    assert principal.role == "customer"
    assert "phone = :phone" in contract_session.calls[0][0]
    assert "crypt(:password, password_hash)" in contract_session.calls[0][0]
    assert contract_session.calls[0][1] == {"phone": "13900000101", "password": "12345678"}


@pytest.mark.contract
def test_login_rejects_unknown_or_inactive_credentials(
    client,
    contract_session: ScriptedSession,
) -> None:
    contract_session.results.append(FakeResult())

    response = client.post(
        "/api/v1/auth/login",
        json={"phone": "13900000999", "password": "wrong"},
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert contract_session.calls[0][1] == {"phone": "13900000999", "password": "wrong"}


@pytest.mark.contract
def test_login_rejects_an_ambiguous_phone_without_selecting_an_account(
    client,
    contract_session: ScriptedSession,
) -> None:
    contract_session.results.append(
        FakeResult(
            [
                {
                    "id": USER_ID,
                    "email": "lin@example.com",
                    "display_name": "小林",
                    "role": "customer",
                },
                {
                    "id": UUID("00000000-0000-4000-8000-000000000102"),
                    "email": "chen@example.com",
                    "display_name": "陈晨",
                    "role": "customer",
                },
            ]
        )
    )

    response = client.post(
        "/api/v1/auth/login",
        json={"phone": "13900000101", "password": "12345678"},
    )

    assert response.status_code == 401
