import pytest
from fastapi.testclient import TestClient

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
    with (
        TestClient(app) as client,
        client.websocket_connect("/ws/text/demo-session") as websocket,
    ):
        assert websocket.receive_json() == {
            "type": "session.connected",
            "sessionId": "demo-session",
        }
