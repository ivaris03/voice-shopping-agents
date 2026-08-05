from collections import defaultdict, deque
from uuid import UUID

import pytest
from fastapi import HTTPException

from voice_shopping_api.realtime import hub as realtime_module
from voice_shopping_api.realtime.events import event_envelope
from voice_shopping_api.realtime.router import _audio_error_payload

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
