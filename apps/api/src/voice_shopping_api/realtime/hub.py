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
from voice_shopping_api.modules.sessions.service import finalize_session_profile
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
        self.journals: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=300))
        self.locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.redis = Redis.from_url(get_settings().redis_url, decode_responses=True)

    async def publish_text(self, session_id: str, events: list[dict[str, Any]]) -> None:
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
        for connection in tuple(self.text_connections[session_id]):
            try:
                for event in events:
                    await connection.send_json(event)
            except (RuntimeError, WebSocketDisconnect):
                stale.append(connection)
        for connection in stale:
            self.text_connections[session_id].discard(connection)

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
        if self.text_connections[session_key] or self.audio_connections[session_key]:
            return
        async with self.locks[session_key], async_session_factory() as db_session:
            if self.text_connections[session_key] or self.audio_connections[session_key]:
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

    async def _send_audio_event(self, session_id: str, event: dict[str, Any]) -> None:
        for connection in tuple(self.audio_connections[session_id]):
            try:
                await connection.send_json(event)
            except (RuntimeError, WebSocketDisconnect):
                self.audio_connections[session_id].discard(connection)

    async def _send_audio_chunk(self, session_id: str, chunk: bytes | None) -> None:
        if chunk is None:
            return
        for connection in tuple(self.audio_connections[session_id]):
            try:
                await connection.send_bytes(chunk)
            except (RuntimeError, WebSocketDisconnect):
                self.audio_connections[session_id].discard(connection)

    async def publish_audio_sentence(
        self,
        session_id: str,
        turn_id: str,
        sentence: str,
        sentence_index: int,
        sentence_count: int | None = None,
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
            {
                "type": "audio.start",
                "sessionId": session_id,
                "turnId": turn_id,
                "seq": sentence_index * 2 - 1,
                "payload": start_payload,
            },
        )
        await self._send_audio_chunk(session_id, _silent_wav() if use_fallback else first_chunk)
        if not use_fallback:
            async for chunk in stream:
                await self._send_audio_chunk(session_id, chunk)
        await self._send_audio_event(
            session_id,
            {
                "type": "audio.end",
                "sessionId": session_id,
                "turnId": turn_id,
                "seq": sentence_index * 2,
                "payload": end_payload,
            },
        )

    async def publish_audio_done(
        self, session_id: str, turn_id: str, sentence_count: int
    ) -> None:
        if not self.audio_connections[session_id]:
            return
        await self._send_audio_event(
            session_id,
            {
                "type": "audio.done",
                "sessionId": session_id,
                "turnId": turn_id,
                "seq": sentence_count * 2 + 1,
                "payload": {"sentenceCount": sentence_count},
            },
        )

    async def publish_audio(self, session_id: str, turn_id: str, text_value: str) -> None:
        sentences = split_sentences(text_value)
        for sentence_index, sentence in enumerate(sentences, start=1):
            await self.publish_audio_sentence(
                session_id,
                turn_id,
                sentence,
                sentence_index,
                len(sentences),
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

        async def publish_streamed_sentence(sentence: str) -> None:
            nonlocal streamed_sentence_count
            streamed_sentence_count += 1
            await self.publish_audio_sentence(
                session_id,
                turn_id,
                sentence,
                streamed_sentence_count,
            )

        async with self.locks[session_id], async_session_factory() as db_session:
            state, events = await process_turn(
                db_session,
                session_id,
                turn_id,
                utterance,
                user_id,
                on_events=lambda batch: self.publish_text(session_id, batch),
                on_speech_sentence=publish_streamed_sentence,
                profile_updates=profile_updates,
            )
        if events:
            await self.publish_text(session_id, events)
        if state.get("speech_audio_streamed") and streamed_sentence_count:
            await self.publish_audio_done(session_id, turn_id, streamed_sentence_count)
        else:
            await self.publish_audio(session_id, turn_id, state.get("final_reply", ""))


hub = RealtimeHub()
