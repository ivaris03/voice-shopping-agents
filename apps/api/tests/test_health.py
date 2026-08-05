from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import voice_shopping_api.realtime.router as realtime_router
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
            "turnId": "session",
            "seq": 0,
            "payload": {},
        }


@pytest.mark.contract
def test_session_closed_uses_the_session_event_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, _ = create_access_token(
        Principal(
            user_id=UUID("00000000-0000-4000-8000-000000000101"),
            email="lin@example.com",
            display_name="小林",
            role="customer",
        )
    )

    async def fake_close_session(*_args, **_kwargs):
        return {
            "sessionId": "demo-session",
            "status": "closed",
            "updatedFields": [],
        }

    async def skip_disconnect_finalization(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(realtime_router.hub, "close_session", fake_close_session)
    monkeypatch.setattr(
        realtime_router.hub,
        "finalize_disconnected_session",
        skip_disconnect_finalization,
    )

    with (
        TestClient(app) as client,
        client.websocket_connect(f"/ws/text/demo-session?token={token}") as websocket,
    ):
        websocket.receive_json()
        websocket.send_json({"type": "session.close"})
        assert websocket.receive_json() == {
            "type": "session.closed",
            "sessionId": "demo-session",
            "turnId": "session",
            "seq": 0,
            "payload": {
                "sessionId": "demo-session",
                "status": "closed",
                "updatedFields": [],
            },
        }


@pytest.mark.contract
def test_audio_websocket_control_events_use_the_shared_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, _ = create_access_token(
        Principal(
            user_id=UUID("00000000-0000-4000-8000-000000000101"),
            email="lin@example.com",
            display_name="小林",
            role="customer",
        )
    )

    async def skip_disconnect_finalization(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(
        realtime_router,
        "get_settings",
        lambda: SimpleNamespace(dashscope_api_key=None),
    )
    monkeypatch.setattr(
        realtime_router.hub,
        "finalize_disconnected_session",
        skip_disconnect_finalization,
    )

    with (
        TestClient(app) as client,
        client.websocket_connect(f"/ws/audio/demo-session?token={token}") as websocket,
    ):
        assert websocket.receive_json() == {
            "type": "audio.ready",
            "sessionId": "demo-session",
            "turnId": "session",
            "seq": 0,
            "payload": {},
        }
        websocket.send_json({"type": "audio.start", "turnId": "voice-turn-001"})
        assert websocket.receive_json() == {
            "type": "audio.error",
            "sessionId": "demo-session",
            "turnId": "voice-turn-001",
            "seq": 1,
            "payload": {
                "message": "服务端未配置 ASR 模型",
                "stage": "asr_start",
            },
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
