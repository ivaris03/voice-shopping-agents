"""DashScope streaming TTS adapter."""

import asyncio
import logging
from collections.abc import AsyncIterator
from time import perf_counter
from typing import Any

import dashscope
from dashscope.audio.http_tts.http_speech_synthesizer import HttpSpeechSynthesizer

from voice_shopping_api.core.config import get_settings
from voice_shopping_api.core.observability import finish_trace, start_trace

logger = logging.getLogger(__name__)

__all__ = ["synthesize_chunks"]


async def synthesize_chunks(
    text_value: str,
    *,
    session_id: str | None = None,
    turn_id: str | None = None,
) -> AsyncIterator[bytes]:
    """Yield provider audio chunks without coupling TTS to WebSocket transport."""
    settings = get_settings()
    started = perf_counter()
    trace_metadata: dict[str, Any] = {
        "ls_provider": "dashscope",
        "ls_model_name": settings.tts_model,
        "operation": "tts",
        "channel": "audio",
        "text": text_value,
        "text_length": len(text_value),
        "sample_rate": 24000,
        "session_id": session_id,
        "turn_id": turn_id,
    }
    span = start_trace(
        "dashscope-tts",
        run_type="llm",
        inputs={
            "text": text_value,
            "audio_format": "wav",
            "sample_rate": 24000,
            "session_id": session_id,
            "turn_id": turn_id,
        },
        metadata=trace_metadata,
        tags=["dashscope", "tts", "audio"],
        project_name=settings.langsmith_project,
    )
    trace_closed = False
    chunk_count = 0
    audio_bytes = 0
    request_id = ""
    provider_usage: dict[str, Any] | None = None

    def finish_span(status: str, error: BaseException | None = None) -> None:
        nonlocal trace_closed
        if trace_closed:
            return
        trace_closed = True
        metadata: dict[str, Any] = {
            "status": status,
            "duration_ms": round((perf_counter() - started) * 1000, 2),
            "chunk_count": chunk_count,
            "audio_bytes": audio_bytes,
            "text": text_value,
            "text_length": len(text_value),
            # DashScope TTS bills characters; retain the safe count as metadata.
            "usage_characters": len(text_value),
        }
        if request_id:
            metadata["request_id"] = request_id
        if provider_usage:
            metadata["dashscope_usage"] = dict(provider_usage)
        finish_trace(
            span,
            outputs=None
            if error is not None
            else {"chunk_count": chunk_count, "audio_bytes": audio_bytes},
            metadata=metadata,
            usage=provider_usage,
            error=error,
        )

    if not settings.dashscope_api_key or not text_value:
        finish_span("skipped")
        return
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    errors: list[Exception] = []
    loop = asyncio.get_running_loop()

    def generate() -> None:
        nonlocal audio_bytes, chunk_count, provider_usage, request_id
        try:
            dashscope.api_key = settings.dashscope_api_key
            dashscope.base_http_api_url = settings.dashscope_http_base_url
            stream = HttpSpeechSynthesizer.call(
                model=settings.tts_model,
                text=text_value,
                voice=settings.tts_voice,
                audio_format="wav",
                sample_rate=24000,
                stream=True,
                api_key=settings.dashscope_api_key,
            )
            for chunk in stream:
                request_id = getattr(chunk, "request_id", "") or request_id
                chunk_usage = getattr(chunk, "usage", None)
                if isinstance(chunk_usage, dict):
                    provider_usage = dict(chunk_usage)
                if not chunk.audio_url and chunk.audio_data:
                    data = bytes(chunk.audio_data)
                    audio_bytes += len(data)
                    chunk_count += 1
                    loop.call_soon_threadsafe(queue.put_nowait, data)
        except Exception as exc:
            errors.append(exc)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    try:
        worker = asyncio.create_task(asyncio.to_thread(generate))
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield chunk
        await worker
        if errors:
            finish_span("error", errors[0])
            logger.warning("TTS failed; browser fallback will be used: %s", errors[0])
        else:
            finish_span("ok" if chunk_count else "empty")
    finally:
        if not trace_closed:
            finish_span("cancelled", asyncio.CancelledError())
