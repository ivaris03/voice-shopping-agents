import json
from collections import defaultdict, deque
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from voice_shopping_api.realtime import hub as realtime_module
from voice_shopping_api.realtime import router as realtime_router
from voice_shopping_api.realtime.events import event_envelope
from voice_shopping_api.realtime.router import _audio_error_payload, _profile_updates

USER_ID = UUID("00000000-0000-4000-8000-000000000101")
OTHER_USER_ID = UUID("00000000-0000-4000-8000-000000000102")


class SessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_args):
        return None


class Connection:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        self.chunks: list[bytes] = []

    async def send_json(self, event: dict[str, object]) -> None:
        self.events.append(event)

    async def send_bytes(self, chunk: bytes) -> None:
        self.chunks.append(chunk)


class AudioSocket:
    def __init__(self, messages: list[dict[str, object]]) -> None:
        self._messages = iter(messages)
        self.events: list[dict[str, object]] = []
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def receive(self) -> dict[str, object]:
        return next(self._messages)

    async def send_json(self, event: dict[str, object]) -> None:
        self.events.append(event)


class CompletedAsr:
    def __init__(self, **_kwargs) -> None:
        return

    async def start(self) -> None:
        return

    async def stop(self) -> str:
        return "服务端转写"

    async def next_transcript_update(self) -> None:
        return None


class FailingWorkflowHub:
    def __init__(self) -> None:
        self.run_turn_args: tuple[object, ...] | None = None
        self.finalized: tuple[str, UUID] | None = None

    def register_audio_connection(self, *_args) -> None:
        return

    def unregister_audio_connection(self, *_args) -> None:
        return

    async def finalize_disconnected_session(self, session_id: str, user_id: UUID) -> None:
        self.finalized = (session_id, user_id)

    async def run_turn(self, *args) -> None:
        self.run_turn_args = args
        raise RuntimeError("database unavailable")


class RedisPipeline:
    def rpush(self, *_args) -> None:
        return None

    def ltrim(self, *_args) -> None:
        return None

    def expire(self, *_args) -> None:
        return None

    async def execute(self) -> None:
        return None


class Redis:
    def pipeline(self) -> RedisPipeline:
        return RedisPipeline()


def _hub_with_journal() -> realtime_module.RealtimeHub:
    instance = realtime_module.RealtimeHub.__new__(realtime_module.RealtimeHub)
    instance.journals = defaultdict(lambda: deque(maxlen=300))
    instance.journals["session-key"].append(
        event_envelope("text.completed", "session-key", "turn-1", 1)
    )
    return instance


@pytest.mark.asyncio
async def test_live_events_only_reach_connections_bound_to_the_session_owner() -> None:
    hub = _hub_with_journal()
    hub.text_connections = defaultdict(set)
    hub.text_connection_users = defaultdict(dict)
    hub.redis = Redis()
    owner_connection = Connection()
    foreign_connection = Connection()
    hub.register_text_connection("session-key", owner_connection, USER_ID)
    hub.register_text_connection("session-key", foreign_connection, OTHER_USER_ID)

    event = event_envelope("text.completed", "session-key", "turn-1", 2)
    await hub.publish_text("session-key", [event], USER_ID)

    assert owner_connection.events == [event]
    assert foreign_connection.events == []


@pytest.mark.asyncio
async def test_live_audio_only_reaches_connections_bound_to_the_session_owner() -> None:
    hub = _hub_with_journal()
    hub.audio_connections = defaultdict(set)
    hub.audio_connection_users = defaultdict(dict)
    owner_connection = Connection()
    foreign_connection = Connection()
    hub.register_audio_connection("session-key", owner_connection, USER_ID)
    hub.register_audio_connection("session-key", foreign_connection, OTHER_USER_ID)

    event = event_envelope("audio.start", "session-key", "turn-1", 1)
    await hub._send_audio_event("session-key", event, USER_ID)
    await hub._send_audio_chunk("session-key", b"audio", USER_ID)

    assert owner_connection.events == [event]
    assert owner_connection.chunks == [b"audio"]
    assert foreign_connection.events == []
    assert foreign_connection.chunks == []


def test_foreign_connections_do_not_keep_the_owners_session_open() -> None:
    hub = _hub_with_journal()
    hub.text_connections = defaultdict(set)
    hub.audio_connections = defaultdict(set)
    hub.text_connection_users = defaultdict(dict)
    hub.audio_connection_users = defaultdict(dict)
    hub.register_text_connection("session-key", Connection(), OTHER_USER_ID)

    assert hub._has_connections_for_user("session-key", USER_ID) is False
    assert hub._has_connections_for_user("session-key", OTHER_USER_ID) is True


def test_audio_error_event_uses_the_envelope_and_preserves_capture_metrics() -> None:
    event = event_envelope(
        "audio.error",
        "session-key",
        "turn-1",
        3,
        _audio_error_payload(
            "会话已关闭，无法继续操作",
            stage="workflow",
            received_bytes=4096,
            client_metrics={"peak": 0.12, "durationMs": 1200},
        ),
    )

    assert event == {
        "type": "audio.error",
        "sessionId": "session-key",
        "turnId": "turn-1",
        "seq": 3,
        "payload": {
            "message": "会话已关闭，无法继续操作",
            "stage": "workflow",
            "receivedBytes": 4096,
            "clientMetrics": {"peak": 0.12, "durationMs": 1200},
        },
    }


@pytest.mark.parametrize("field", ["budget", "budgetBand", "budget_band"])
def test_websocket_profile_rejects_budget_fields(field: str) -> None:
    with pytest.raises(HTTPException) as error:
        _profile_updates({"profile": {field: 2000}})

    assert error.value.status_code == 422
    assert "budgetMax" in str(error.value.detail)


@pytest.mark.asyncio
async def test_audio_workflow_exception_sends_audio_error(monkeypatch: pytest.MonkeyPatch) -> None:
    realtime_hub = FailingWorkflowHub()
    websocket = AudioSocket(
        [
            {
                "type": "websocket.receive",
                "text": json.dumps({"type": "audio.start", "turnId": "voice-turn"}),
            },
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {
                        "type": "audio.commit",
                        "turnId": "voice-turn",
                        "clientMetrics": {"durationMs": 1200},
                    }
                ),
            },
            {"type": "websocket.disconnect"},
        ]
    )

    async def customer_user_id(_websocket) -> UUID:
        return USER_ID

    monkeypatch.setattr(realtime_router, "_customer_user_id", customer_user_id)
    monkeypatch.setattr(realtime_router, "StreamingAsr", CompletedAsr)
    monkeypatch.setattr(
        realtime_router,
        "get_settings",
        lambda: SimpleNamespace(dashscope_api_key="test-key", asr_model="test-asr"),
    )
    monkeypatch.setattr(realtime_router, "hub", realtime_hub)

    await realtime_router.audio_socket(websocket, "session-key")

    assert websocket.accepted is True
    assert realtime_hub.run_turn_args == ("session-key", "voice-turn", "服务端转写", USER_ID)
    assert websocket.events[-1] == {
        "type": "audio.error",
        "sessionId": "session-key",
        "turnId": "voice-turn",
        "seq": 3,
        "payload": {
            "message": "导购工作流处理失败，请稍后重试",
            "stage": "workflow",
            "receivedBytes": 0,
            "clientMetrics": {"durationMs": 1200},
        },
    }


@pytest.mark.asyncio
async def test_replay_for_user_authorizes_before_reading_the_journal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[UUID, UUID]] = []

    async def fake_get_session(session, session_id, user_id, **_kwargs):
        calls.append((session_id, user_id))
        return {"id": session_id, "user_id": user_id, "status": "active"}

    monkeypatch.setattr(realtime_module, "async_session_factory", lambda: SessionContext())
    monkeypatch.setattr(realtime_module, "get_session_for_user", fake_get_session)

    result = await _hub_with_journal().replay_for_user("session-key", USER_ID, "turn-1", 0)

    assert result[0]["type"] == "text.completed"
    assert calls == [(realtime_module.stable_uuid("session-key"), USER_ID)]


@pytest.mark.asyncio
async def test_replay_for_user_does_not_read_a_foreign_session_journal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def reject_foreign_session(*_args, **_kwargs):
        raise HTTPException(status_code=404, detail="会话不存在或无权访问")

    monkeypatch.setattr(realtime_module, "async_session_factory", lambda: SessionContext())
    monkeypatch.setattr(realtime_module, "get_session_for_user", reject_foreign_session)

    with pytest.raises(HTTPException) as error:
        await _hub_with_journal().replay_for_user("session-key", USER_ID, "turn-1", 0)

    assert error.value.status_code == 404
