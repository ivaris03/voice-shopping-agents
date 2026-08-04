"""Text and audio WebSocket transports."""

import asyncio
import json
import logging
from contextlib import suppress
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from voice_shopping_api.core.config import get_settings
from voice_shopping_api.core.identity import DEFAULT_CUSTOMER_ID
from voice_shopping_api.realtime.asr import StreamingAsr
from voice_shopping_api.realtime.hub import RealtimeHub, hub

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


@router.websocket("/ws/text/{session_id}")
async def text_socket(websocket: WebSocket, session_id: str) -> None:
    user_id = _user_id(websocket)
    await websocket.accept()
    hub.register_text_connection(session_id, websocket, user_id)
    await websocket.send_json({"type": "session.connected", "sessionId": session_id})
    try:
        while True:
            message = await websocket.receive_json()
            message_type = message.get("type", "turn.submit")
            if message_type == "session.resume":
                turn_id = message.get("turnId")
                after_seq = int(message.get("afterSeq", 0))
                try:
                    replay = await hub.replay_for_user(
                        session_id, user_id, turn_id, after_seq
                    )
                except HTTPException as exc:
                    await websocket.send_json(
                        {
                            "type": "flow.error",
                            "sessionId": session_id,
                            "turnId": str(turn_id or "resume"),
                            "seq": 0,
                            "payload": {"message": str(exc.detail)},
                        }
                    )
                    continue
                for event in replay:
                    await websocket.send_json(event)
                continue
            if message_type == "session.close":
                try:
                    result = await hub.close_session(
                        session_id,
                        user_id,
                        message.get("profile")
                        if isinstance(message.get("profile"), dict)
                        else None,
                    )
                except HTTPException as exc:
                    await websocket.send_json(
                        {
                            "type": "flow.error",
                            "sessionId": session_id,
                            "turnId": "close",
                            "seq": 0,
                            "payload": {"message": str(exc.detail)},
                        }
                    )
                    continue
                await websocket.send_json(
                    {
                        "type": "session.closed",
                        "sessionId": session_id,
                        "payload": result,
                    }
                )
                break
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
            try:
                await hub.run_turn(
                    session_id,
                    turn_id,
                    utterance,
                    user_id,
                    message.get("profile")
                    if isinstance(message.get("profile"), dict)
                    else None,
                )
            except HTTPException as exc:
                await websocket.send_json(
                    {
                        "type": "flow.error",
                        "sessionId": session_id,
                        "turnId": turn_id,
                        "seq": 0,
                        "payload": {"message": str(exc.detail)},
                    }
                )
    except WebSocketDisconnect:
        hub.unregister_text_connection(session_id, websocket)
    except Exception as exc:
        hub.unregister_text_connection(session_id, websocket)
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
    finally:
        hub.unregister_text_connection(session_id, websocket)
        await hub.finalize_disconnected_session(session_id, user_id)


@router.websocket("/ws/audio/{session_id}")
async def audio_socket(websocket: WebSocket, session_id: str) -> None:
    user_id = _user_id(websocket)
    await websocket.accept()
    hub.register_audio_connection(session_id, websocket, user_id)
    await websocket.send_json({"type": "audio.ready", "sessionId": session_id})
    received_bytes = 0
    asr: StreamingAsr | None = None
    sentence_task: asyncio.Task[None] | None = None
    send_lock = asyncio.Lock()

    async def send_json(event: dict[str, Any]) -> None:
        async with send_lock:
            await websocket.send_json(event)

    async def publish_completed_sentences(current_asr: StreamingAsr, turn_id: str) -> None:
        while (sentence := await current_asr.next_completed_sentence()) is not None:
            await send_json(
                {
                    "type": "asr.sentence",
                    "sessionId": session_id,
                    "turnId": turn_id,
                    "payload": {"transcript": sentence},
                }
            )

    async def wait_for_sentence_task() -> None:
        nonlocal sentence_task
        if sentence_task is None:
            return
        try:
            await sentence_task
        finally:
            sentence_task = None

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
                    await send_json(
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
                        await wait_for_sentence_task()
                    asr = StreamingAsr(session_id=session_id, turn_id=turn_id)
                    await asr.start()
                except Exception as exc:
                    logger.exception("ASR failed to start for session %s", session_id)
                    asr = None
                    await send_json(
                        {
                            "type": "audio.error",
                            "sessionId": session_id,
                            "turnId": turn_id,
                            "payload": {"message": f"ASR 模型启动失败：{exc}"},
                        }
                    )
                    continue
                sentence_task = asyncio.create_task(publish_completed_sentences(asr, turn_id))
                await send_json(
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
                    await wait_for_sentence_task()
                except Exception as exc:
                    logger.exception("ASR failed to finish for session %s", session_id)
                    asr = None
                    await send_json(
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
                    await send_json(
                        {
                            "type": "asr.completed",
                            "sessionId": session_id,
                            "turnId": turn_id,
                            "payload": {"transcript": transcript},
                        }
                    )
                    try:
                        await hub.run_turn(session_id, turn_id, transcript, user_id)
                    except HTTPException as exc:
                        await send_json(
                            {
                                "type": "audio.error",
                                "sessionId": session_id,
                                "turnId": turn_id,
                                "payload": {"message": str(exc.detail)},
                            }
                        )
                else:
                    logger.warning(
                        "ASR returned no transcript for session %s: bytes=%s client=%s",
                        session_id,
                        received_bytes,
                        client_metrics,
                    )
                    await send_json(
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
                await wait_for_sentence_task()
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
        if sentence_task is not None:
            with suppress(Exception):
                await wait_for_sentence_task()
        hub.unregister_audio_connection(session_id, websocket)
        await hub.finalize_disconnected_session(session_id, user_id)


__all__ = ["RealtimeHub", "audio_socket", "hub", "router", "text_socket"]
