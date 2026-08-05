from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from voice_shopping_api.core.identity import Principal, create_access_token
from voice_shopping_api.main import app


@pytest.mark.contract
def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "Voice Shopping API",
        "version": "0.1.0",
    }


@pytest.mark.contract
def test_text_websocket_handshake() -> None:
    token, _ = create_access_token(
        Principal(
            user_id=UUID("00000000-0000-4000-8000-000000000101"),
            email="lin@example.com",
            display_name="小林",
            role="customer",
        )
    )
    with (
        TestClient(app) as client,
        client.websocket_connect(f"/ws/text/demo-session?token={token}") as websocket,
    ):
        assert websocket.receive_json() == {
            "type": "session.connected",
            "sessionId": "demo-session",
        }


@pytest.mark.contract
def test_text_websocket_requires_a_token() -> None:
    with (
        TestClient(app) as client,
        pytest.raises(WebSocketDisconnect) as error,
        client.websocket_connect("/ws/text/demo-session"),
    ):
        pass

    assert error.value.code == 4401
