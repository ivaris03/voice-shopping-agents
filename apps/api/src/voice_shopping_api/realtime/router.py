"""Text and audio WebSocket transports."""

import asyncio
import json
import logging
from contextlib import suppress
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from voice_shopping_api.core.config import get_settings
from voice_shopping_api.core.identity import websocket_customer_principal
from voice_shopping_api.realtime.asr import StreamingAsr
from voice_shopping_api.realtime.events import (
    SESSION_EVENT_SEQ,
    SESSION_TURN_ID,
    event_envelope,
)
from voice_shopping_api.realtime.hub import RealtimeHub, hub

router = APIRouter()
logger = logging.getLogger(__name__)


async def _customer_user_id(websocket: WebSocket) -> UUID | None:
    token = websocket.query_params.get("token")
    try:
        return websocket_customer_principal(token).user_id
    except HTTPException as exc:
        await websocket.close(code=4401 if exc.status_code == 401 else 4403)
        return None


def _flow_error_event(session_id: str, turn_id: str, message: str) -> dict[str, Any]:
    return event_envelope(
        "flow.error",
        session_id,
        turn_id,
        0,
        {"message": message},
    )


def _audio_error_payload(
    message: str,
    *,
    stage: str,
    received_bytes: int | None = None,
    client_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an audio error payload without losing where the failure occurred.

    ASR failures and downstream workflow failures share the audio transport,
    but only the former should be interpreted as microphone diagnostics by the
    client.  Keep capture metrics when they are available so the client can
    distinguish the two cases.
    """
    payload: dict[str, Any] = {"message": message, "stage": stage}
    if received_bytes is not None:
        payload["receivedBytes"] = received_bytes
    if client_metrics is not None:
        payload["clientMetrics"] = client_metrics
    return payload


@router.websocket("/ws/text/{session_id}")
async def text_socket(websocket: WebSocket, session_id: str) -> None:
    user_id = await _customer_user_id(websocket)
    if user_id is None:
        return
    await websocket.accept()
    hub.register_text_connection(session_id, websocket, user_id)
    await websocket.send_json(
        event_envelope(
            "session.connected",
            session_id,
            SESSION_TURN_ID,
            SESSION_EVENT_SEQ,
        )
    )
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
                        _flow_error_event(
                            session_id,
                            str(turn_id or "resume"),
                            str(exc.detail),
                        )
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
                        _flow_error_event(session_id, "close", str(exc.detail))
                    )
                    continue
                await websocket.send_json(
                    event_envelope(
                        "session.closed",
                        session_id,
                        SESSION_TURN_ID,
                        SESSION_EVENT_SEQ,
                        result,
                    )
                )
                break
            turn_id = str(message.get("turnId") or "turn-1")
            utterance = str(message.get("utterance") or "").strip()
            if not utterance:
                await websocket.send_json(
                    _flow_error_event(session_id, turn_id, "utterance 不能为空")
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
                    _flow_error_event(session_id, turn_id, str(exc.detail))
                )
    except WebSocketDisconnect:
        hub.unregister_text_connection(session_id, websocket)
    except Exception as exc:
        hub.unregister_text_connection(session_id, websocket)
        with suppress(RuntimeError):
            await websocket.send_json(_flow_error_event(session_id, "unknown", str(exc)))
    finally:
        hub.unregister_text_connection(session_id, websocket)
        await hub.finalize_disconnected_session(session_id, user_id)


@router.websocket("/ws/audio/{session_id}")
async def audio_socket(websocket: WebSocket, session_id: str) -> None:
    user_id = await _customer_user_id(websocket)
    if user_id is None:
        return
    await websocket.accept()
    hub.register_audio_connection(session_id, websocket, user_id)
    await websocket.send_json(
        event_envelope(
            "audio.ready",
            session_id,
            SESSION_TURN_ID,
            SESSION_EVENT_SEQ,
        )
    )
    received_bytes = 0
    asr: StreamingAsr | None = None
    transcript_task: asyncio.Task[None] | None = None
    send_lock = asyncio.Lock()
    event_sequences: dict[str, int] = {}

    async def reset_event_sequence(turn_id: str) -> None:
        async with send_lock:
            event_sequences[turn_id] = 0

    async def send_event(
        event_type: str,
        turn_id: str,
        payload: dict[str, Any],
    ) -> None:
        async with send_lock:
            sequence = event_sequences.get(turn_id, 0) + 1
            event_sequences[turn_id] = sequence
            await websocket.send_json(
                event_envelope(event_type, session_id, turn_id, sequence, payload)
            )

    async def publish_transcript_updates(current_asr: StreamingAsr, turn_id: str) -> None:
        while (update := await current_asr.next_transcript_update()) is not None:
            kind, transcript, full_transcript = update
            if kind == "partial":
                await send_event("asr.partial", turn_id, {"transcript": transcript})
                continue
            await send_event(
                "asr.sentence",
                turn_id,
                {
                    "transcript": transcript,
                    "fullTranscript": full_transcript,
                },
            )

    async def wait_for_transcript_task() -> None:
        nonlocal transcript_task
        if transcript_task is None:
            return
        try:
            await transcript_task
        finally:
            transcript_task = None

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
                await reset_event_sequence(turn_id)
                if not get_settings().dashscope_api_key:
                    await send_event(
                        "audio.error",
                        turn_id,
                        _audio_error_payload(
                            "服务端未配置 ASR 模型",
                            stage="asr_start",
                        ),
                    )
                    continue
                try:
                    if asr is not None:
                        with suppress(Exception):
                            await asr.stop()
                        await wait_for_transcript_task()
                    asr = StreamingAsr(session_id=session_id, turn_id=turn_id)
                    await asr.start()
                except Exception as exc:
                    logger.exception("ASR failed to start for session %s", session_id)
                    asr = None
                    await send_event(
                        "audio.error",
                        turn_id,
                        _audio_error_payload(
                            f"ASR 模型启动失败：{exc}",
                            stage="asr_start",
                        ),
                    )
                    continue
                await send_event(
                    "asr.started",
                    turn_id,
                    {
                        "model": get_settings().asr_model,
                        "sampleRate": 16_000,
                    },
                )
                transcript_task = asyncio.create_task(publish_transcript_updates(asr, turn_id))
            elif control.get("type") == "audio.commit":
                turn_id = str(control.get("turnId") or "voice-turn")
                client_metrics = control.get("clientMetrics") or {}
                try:
                    server_transcript = await asr.stop() if asr is not None else ""
                    await wait_for_transcript_task()
                except Exception as exc:
                    logger.exception("ASR failed to finish for session %s", session_id)
                    asr = None
                    await send_event(
                        "audio.error",
                        turn_id,
                        _audio_error_payload(
                            f"ASR 转写失败：{exc}",
                            stage="asr_finish",
                            received_bytes=received_bytes,
                            client_metrics=client_metrics,
                        ),
                    )
                    continue
                asr = None
                transcript = server_transcript or str(control.get("transcript") or "").strip()
                if transcript:
                    await send_event(
                        "asr.completed",
                        turn_id,
                        {"transcript": transcript},
                    )
                    try:
                        await hub.run_turn(session_id, turn_id, transcript, user_id)
                    except HTTPException as exc:
                        await send_event(
                            "audio.error",
                            turn_id,
                            _audio_error_payload(
                                str(exc.detail),
                                stage="workflow",
                                received_bytes=received_bytes,
                                client_metrics=client_metrics,
                            ),
                        )
                else:
                    logger.warning(
                        "ASR returned no transcript for session %s: bytes=%s client=%s",
                        session_id,
                        received_bytes,
                        client_metrics,
                    )
                    await send_event(
                        "audio.error",
                        turn_id,
                        _audio_error_payload(
                            "ASR 未识别到有效语音，请靠近麦克风后重试",
                            stage="asr",
                            received_bytes=received_bytes,
                            client_metrics=client_metrics,
                        ),
                    )
            elif control.get("type") == "audio.cancel":
                if asr is not None:
                    with suppress(Exception):
                        await asr.stop()
                await wait_for_transcript_task()
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
        if transcript_task is not None:
            with suppress(Exception):
                await wait_for_transcript_task()
        hub.unregister_audio_connection(session_id, websocket)
        await hub.finalize_disconnected_session(session_id, user_id)


__all__ = ["RealtimeHub", "audio_socket", "hub", "router", "text_socket"]
