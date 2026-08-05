"""Session-scoped realtime coordination and event delivery."""

import asyncio
import io
import json
import logging
import wave
from collections import defaultdict, deque
from typing import Any
from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect
from redis.asyncio import Redis
from redis.exceptions import RedisError

from voice_shopping_api.agents.service import process_turn
from voice_shopping_api.core.config import get_settings
from voice_shopping_api.core.database import async_session_factory
from voice_shopping_api.core.session import stable_uuid
from voice_shopping_api.core.text import split_sentences
from voice_shopping_api.modules.sessions.service import (
    finalize_session_profile,
    get_session_for_user,
)
from voice_shopping_api.realtime.events import event_envelope
from voice_shopping_api.realtime.tts import synthesize_chunks

logger = logging.getLogger(__name__)


def _silent_wav(duration_seconds: float = 0.12) -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\x00\x00" * int(16_000 * duration_seconds))
    return stream.getvalue()


class RealtimeHub:
    """Own per-session connections, ordering, replay, and turn coordination."""

    def __init__(self) -> None:
        self.text_connections: dict[str, set[WebSocket]] = defaultdict(set)
        self.audio_connections: dict[str, set[WebSocket]] = defaultdict(set)
        self.text_connection_users: dict[str, dict[WebSocket, UUID]] = defaultdict(dict)
        self.audio_connection_users: dict[str, dict[WebSocket, UUID]] = defaultdict(dict)
        self.journals: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=300))
        self.locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.redis = Redis.from_url(get_settings().redis_url, decode_responses=True)

    def register_text_connection(
        self, session_id: str, connection: WebSocket, user_id: UUID
    ) -> None:
        self.text_connections[session_id].add(connection)
        self.text_connection_users[session_id][connection] = user_id

    def unregister_text_connection(self, session_id: str, connection: WebSocket) -> None:
        self.text_connections[session_id].discard(connection)
        users = getattr(self, "text_connection_users", {})
        if session_id in users:
            users[session_id].pop(connection, None)

    def register_audio_connection(
        self, session_id: str, connection: WebSocket, user_id: UUID
    ) -> None:
        self.audio_connections[session_id].add(connection)
        self.audio_connection_users[session_id][connection] = user_id

    def unregister_audio_connection(self, session_id: str, connection: WebSocket) -> None:
        self.audio_connections[session_id].discard(connection)
        users = getattr(self, "audio_connection_users", {})
        if session_id in users:
            users[session_id].pop(connection, None)

    @staticmethod
    def _connection_matches_user(
        connection: WebSocket,
        users: dict[WebSocket, UUID],
        user_id: UUID | None,
    ) -> bool:
        bound_user = users.get(connection)
        return user_id is None or bound_user is None or bound_user == user_id

    def _has_connections_for_user(self, session_id: str, user_id: UUID) -> bool:
        text_users = getattr(self, "text_connection_users", {}).get(session_id, {})
        audio_users = getattr(self, "audio_connection_users", {}).get(session_id, {})
        return any(
            self._connection_matches_user(connection, text_users, user_id)
            for connection in self.text_connections[session_id]
        ) or any(
            self._connection_matches_user(connection, audio_users, user_id)
            for connection in self.audio_connections[session_id]
        )

    async def publish_text(
        self,
        session_id: str,
        events: list[dict[str, Any]],
        user_id: UUID | None = None,
    ) -> None:
        self.journals[session_id].extend(events)
        try:
            key = f"voice-shopping:events:{session_id}"
            pipeline = self.redis.pipeline()
            for event in events:
                pipeline.rpush(key, json.dumps(event, ensure_ascii=False))
            pipeline.ltrim(key, -300, -1)
            pipeline.expire(key, 3600)
            await pipeline.execute()
        except RedisError:
            pass
        stale: list[WebSocket] = []
        connection_users = getattr(self, "text_connection_users", {}).get(session_id, {})
        for connection in tuple(self.text_connections[session_id]):
            if not self._connection_matches_user(
                connection, connection_users, user_id
            ):
                continue
            try:
                for event in events:
                    await connection.send_json(event)
            except (RuntimeError, WebSocketDisconnect):
                stale.append(connection)
        for connection in stale:
            self.unregister_text_connection(session_id, connection)

    async def replay(
        self, session_id: str, turn_id: str | None, after_seq: int
    ) -> list[dict[str, Any]]:
        events = list(self.journals[session_id])
        if not events:
            try:
                values = await self.redis.lrange(f"voice-shopping:events:{session_id}", 0, -1)
                events = [json.loads(value) for value in values]
            except (RedisError, json.JSONDecodeError):
                events = []
        return [
            event
            for event in events
            if (not turn_id or event.get("turnId") == turn_id) and event.get("seq", 0) > after_seq
        ]

    async def replay_for_user(
        self,
        session_key: str,
        user_id: UUID,
        turn_id: str | None,
        after_seq: int,
    ) -> list[dict[str, Any]]:
        """Authorize a session before returning its replay journal."""
        async with async_session_factory() as db_session:
            await get_session_for_user(
                db_session,
                stable_uuid(session_key),
                user_id,
            )
        return await self.replay(session_key, turn_id, after_seq)

    async def close(self) -> None:
        await self.redis.aclose()

    async def close_session(
        self,
        session_key: str,
        user_id: UUID,
        profile_updates: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session_id = stable_uuid(session_key)
        async with self.locks[session_key], async_session_factory() as db_session:
            result = await finalize_session_profile(
                db_session,
                session_id,
                user_id,
                profile_updates,
                close_session=True,
            )
            await db_session.commit()
            return result

    async def finalize_disconnected_session(self, session_key: str, user_id: UUID) -> None:
        """Best-effort profile write when a page disappears without a close event."""
        if self._has_connections_for_user(session_key, user_id):
            return
        async with self.locks[session_key], async_session_factory() as db_session:
            if self._has_connections_for_user(session_key, user_id):
                return
            try:
                await finalize_session_profile(
                    db_session,
                    stable_uuid(session_key),
                    user_id,
                    close_session=False,
                )
                await db_session.commit()
            except Exception:
                await db_session.rollback()
                logger.exception("Failed to finalize disconnected session %s", session_key)

    async def _send_audio_event(
        self, session_id: str, event: dict[str, Any], user_id: UUID | None = None
    ) -> None:
        connection_users = getattr(self, "audio_connection_users", {}).get(session_id, {})
        for connection in tuple(self.audio_connections[session_id]):
            if not self._connection_matches_user(
                connection, connection_users, user_id
            ):
                continue
            try:
                await connection.send_json(event)
            except (RuntimeError, WebSocketDisconnect):
                self.unregister_audio_connection(session_id, connection)

    async def _send_audio_chunk(
        self, session_id: str, chunk: bytes | None, user_id: UUID | None = None
    ) -> None:
        if chunk is None:
            return
        connection_users = getattr(self, "audio_connection_users", {}).get(session_id, {})
        for connection in tuple(self.audio_connections[session_id]):
            if not self._connection_matches_user(
                connection, connection_users, user_id
            ):
                continue
            try:
                await connection.send_bytes(chunk)
            except (RuntimeError, WebSocketDisconnect):
                self.unregister_audio_connection(session_id, connection)

    async def publish_audio_sentence(
        self,
        session_id: str,
        turn_id: str,
        sentence: str,
        sentence_index: int,
        sentence_count: int | None = None,
        user_id: UUID | None = None,
    ) -> None:
        sentence = sentence.strip()
        if not self.audio_connections[session_id] or not sentence:
            return
        stream = synthesize_chunks(sentence, session_id=session_id, turn_id=turn_id)
        first_chunk = await anext(stream, None)
        use_fallback = first_chunk is None
        start_payload: dict[str, Any] = {
            "format": "wav",
            "sampleRate": 16000 if use_fallback else 24000,
            "fallback": use_fallback,
            "text": sentence,
            "sentenceIndex": sentence_index,
        }
        end_payload: dict[str, Any] = {"sentenceIndex": sentence_index}
        if sentence_count is not None:
            start_payload["sentenceCount"] = sentence_count
            end_payload.update(
                {
                    "sentenceCount": sentence_count,
                    "final": sentence_index == sentence_count,
                }
            )
        await self._send_audio_event(
            session_id,
            event_envelope(
                "audio.start",
                session_id,
                turn_id,
                sentence_index * 2 - 1,
                start_payload,
            ),
            user_id,
        )
        await self._send_audio_chunk(
            session_id, _silent_wav() if use_fallback else first_chunk, user_id
        )
        if not use_fallback:
            async for chunk in stream:
                await self._send_audio_chunk(session_id, chunk, user_id)
        await self._send_audio_event(
            session_id,
            event_envelope(
                "audio.end",
                session_id,
                turn_id,
                sentence_index * 2,
                end_payload,
            ),
            user_id,
        )

    async def publish_audio_done(
        self,
        session_id: str,
        turn_id: str,
        sentence_count: int,
        user_id: UUID | None = None,
    ) -> None:
        if not self.audio_connections[session_id]:
            return
        await self._send_audio_event(
            session_id,
            event_envelope(
                "audio.done",
                session_id,
                turn_id,
                sentence_count * 2 + 1,
                {"sentenceCount": sentence_count},
            ),
            user_id,
        )

    async def publish_audio(
        self,
        session_id: str,
        turn_id: str,
        text_value: str,
        user_id: UUID | None = None,
    ) -> None:
        sentences = split_sentences(text_value)
        for sentence_index, sentence in enumerate(sentences, start=1):
            await self.publish_audio_sentence(
                session_id,
                turn_id,
                sentence,
                sentence_index,
                len(sentences),
                user_id,
            )

    async def run_turn(
        self,
        session_id: str,
        turn_id: str,
        utterance: str,
        user_id: UUID,
        profile_updates: dict[str, Any] | None = None,
    ) -> None:
        streamed_sentence_count = 0

        async def publish_streamed_sentence(
            sentence: str, sentence_index: int, sentence_count: int
        ) -> None:
            nonlocal streamed_sentence_count
            streamed_sentence_count = sentence_index
            await self.publish_audio_sentence(
                session_id,
                turn_id,
                sentence,
                sentence_index,
                sentence_count,
                user_id=user_id,
            )

        async with self.locks[session_id], async_session_factory() as db_session:
            try:
                state, events = await process_turn(
                    db_session,
                    session_id,
                    turn_id,
                    utterance,
                    user_id,
                    on_events=lambda batch: self.publish_text(session_id, batch, user_id),
                    on_speech_sentence=publish_streamed_sentence,
                    profile_updates=profile_updates,
                )
            except Exception:
                await db_session.rollback()
                raise
        if events:
            await self.publish_text(session_id, events, user_id)
        if state.get("speech_audio_streamed") and streamed_sentence_count:
            await self.publish_audio_done(
                session_id, turn_id, streamed_sentence_count, user_id
            )
        else:
            await self.publish_audio(
                session_id, turn_id, state.get("final_reply", ""), user_id
            )


hub = RealtimeHub()
