import asyncio
import io
import json
import logging
import wave
from collections import defaultdict, deque
from contextlib import suppress
from typing import Any
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from redis.asyncio import Redis
from redis.exceptions import RedisError

from voice_shopping_api.agents.service import process_turn
from voice_shopping_api.core.config import get_settings
from voice_shopping_api.core.database import async_session_factory
from voice_shopping_api.core.identity import DEFAULT_CUSTOMER_ID
from voice_shopping_api.realtime.speech import StreamingAsr, synthesize_chunks

router = APIRouter()
logger = logging.getLogger(__name__)


def _user_id(websocket: WebSocket) -> UUID:
    value = websocket.query_params.get("userId")
    if not value:
        return DEFAULT_CUSTOMER_ID
    try:
        return UUID(value)
    except ValueError:
        return DEFAULT_CUSTOMER_ID


def _silent_wav(duration_seconds: float = 0.12) -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\x00\x00" * int(16_000 * duration_seconds))
    return stream.getvalue()


class RealtimeHub:
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

    async def publish_audio(self, session_id: str, turn_id: str, text_value: str) -> None:
        if not self.audio_connections[session_id]:
            return
        stream = synthesize_chunks(text_value)
        first_chunk = await anext(stream, None)
        use_fallback = first_chunk is None
        start = {
            "type": "audio.start",
            "sessionId": session_id,
            "turnId": turn_id,
            "seq": 1,
            "payload": {
                "format": "wav",
                "sampleRate": 16000 if use_fallback else 24000,
                "fallback": use_fallback,
                "text": text_value,
            },
        }
        end = {
            "type": "audio.end",
            "sessionId": session_id,
            "turnId": turn_id,
            "seq": 2,
            "payload": {},
        }
        for connection in tuple(self.audio_connections[session_id]):
            try:
                await connection.send_json(start)
            except (RuntimeError, WebSocketDisconnect):
                self.audio_connections[session_id].discard(connection)

        async def send_chunk(chunk: bytes | None) -> None:
            if chunk is None:
                return
            for connection in tuple(self.audio_connections[session_id]):
                try:
                    await connection.send_bytes(chunk)
                except (RuntimeError, WebSocketDisconnect):
                    self.audio_connections[session_id].discard(connection)

        await send_chunk(_silent_wav() if use_fallback else first_chunk)
        if not use_fallback:
            async for chunk in stream:
                await send_chunk(chunk)
        for connection in tuple(self.audio_connections[session_id]):
            try:
                await connection.send_json(end)
            except (RuntimeError, WebSocketDisconnect):
                self.audio_connections[session_id].discard(connection)

    async def run_turn(
        self,
        session_id: str,
        turn_id: str,
        utterance: str,
        user_id: UUID,
    ) -> None:
        async with self.locks[session_id], async_session_factory() as db_session:
            state, events = await process_turn(
                db_session,
                session_id,
                turn_id,
                utterance,
                user_id,
                on_events=lambda batch: self.publish_text(session_id, batch),
            )
        if events:
            await self.publish_text(session_id, events)
        await self.publish_audio(session_id, turn_id, state.get("final_reply", ""))


hub = RealtimeHub()


@router.websocket("/ws/text/{session_id}")
async def text_socket(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    hub.text_connections[session_id].add(websocket)
    await websocket.send_json({"type": "session.connected", "sessionId": session_id})
    try:
        while True:
            message = await websocket.receive_json()
            message_type = message.get("type", "turn.submit")
            if message_type == "session.resume":
                turn_id = message.get("turnId")
                after_seq = int(message.get("afterSeq", 0))
                for event in await hub.replay(session_id, turn_id, after_seq):
                    await websocket.send_json(event)
                continue
            turn_id = str(message.get("turnId") or "turn-1")
            utterance = str(message.get("utterance") or "").strip()
            if not utterance:
                await websocket.send_json(
                    {
                        "type": "flow.error",
                        "sessionId": session_id,
                        "turnId": turn_id,
                        "seq": 0,
                        "payload": {"message": "utterance 不能为空"},
                    }
                )
                continue
            await hub.run_turn(session_id, turn_id, utterance, _user_id(websocket))
    except WebSocketDisconnect:
        hub.text_connections[session_id].discard(websocket)
    except Exception as exc:
        hub.text_connections[session_id].discard(websocket)
        with suppress(RuntimeError):
            await websocket.send_json(
                {
                    "type": "flow.error",
                    "sessionId": session_id,
                    "turnId": "unknown",
                    "seq": 0,
                    "payload": {"message": str(exc)},
                }
            )


@router.websocket("/ws/audio/{session_id}")
async def audio_socket(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    hub.audio_connections[session_id].add(websocket)
    await websocket.send_json({"type": "audio.ready", "sessionId": session_id})
    received_bytes = 0
    asr: StreamingAsr | None = None
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                received_bytes += len(message["bytes"])
                if asr is not None:
                    await asr.send(message["bytes"])
                continue
            if message.get("text") is None:
                continue
            control = json.loads(message["text"])
            if control.get("type") == "audio.start":
                received_bytes = 0
                turn_id = str(control.get("turnId") or "voice-turn")
                if not get_settings().dashscope_api_key:
                    await websocket.send_json(
                        {
                            "type": "audio.error",
                            "sessionId": session_id,
                            "turnId": turn_id,
                            "payload": {"message": "服务端未配置 ASR 模型"},
                        }
                    )
                    continue
                try:
                    if asr is not None:
                        with suppress(Exception):
                            await asr.stop()
                    asr = StreamingAsr()
                    await asr.start()
                except Exception as exc:
                    logger.exception("ASR failed to start for session %s", session_id)
                    asr = None
                    await websocket.send_json(
                        {
                            "type": "audio.error",
                            "sessionId": session_id,
                            "turnId": turn_id,
                            "payload": {"message": f"ASR 模型启动失败：{exc}"},
                        }
                    )
                    continue
                await websocket.send_json(
                    {
                        "type": "asr.started",
                        "sessionId": session_id,
                        "turnId": turn_id,
                        "payload": {
                            "model": get_settings().asr_model,
                            "sampleRate": 16_000,
                        },
                    }
                )
            elif control.get("type") == "audio.commit":
                turn_id = str(control.get("turnId") or "voice-turn")
                client_metrics = control.get("clientMetrics") or {}
                try:
                    server_transcript = await asr.stop() if asr is not None else ""
                except Exception as exc:
                    logger.exception("ASR failed to finish for session %s", session_id)
                    asr = None
                    await websocket.send_json(
                        {
                            "type": "audio.error",
                            "sessionId": session_id,
                            "turnId": turn_id,
                            "payload": {
                                "message": f"ASR 转写失败：{exc}",
                                "receivedBytes": received_bytes,
                            },
                        }
                    )
                    continue
                asr = None
                transcript = server_transcript or str(control.get("transcript") or "").strip()
                if transcript:
                    await websocket.send_json(
                        {
                            "type": "asr.completed",
                            "sessionId": session_id,
                            "turnId": turn_id,
                            "payload": {"transcript": transcript},
                        }
                    )
                    await hub.run_turn(session_id, turn_id, transcript, _user_id(websocket))
                else:
                    logger.warning(
                        "ASR returned no transcript for session %s: bytes=%s client=%s",
                        session_id,
                        received_bytes,
                        client_metrics,
                    )
                    await websocket.send_json(
                        {
                            "type": "audio.error",
                            "sessionId": session_id,
                            "turnId": turn_id,
                            "payload": {
                                "message": "ASR 未识别到有效语音，请靠近麦克风后重试",
                                "receivedBytes": received_bytes,
                                "clientMetrics": client_metrics,
                            },
                        }
                    )
            elif control.get("type") == "audio.cancel":
                if asr is not None:
                    with suppress(Exception):
                        await asr.stop()
                asr = None
                received_bytes = 0
    except (RuntimeError, WebSocketDisconnect):
        pass
    except Exception:
        logger.exception("Unexpected audio socket failure for session %s", session_id)
    finally:
        if asr is not None:
            with suppress(Exception):
                await asr.stop()
        hub.audio_connections[session_id].discard(websocket)
