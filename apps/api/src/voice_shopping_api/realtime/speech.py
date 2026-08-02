import asyncio
import logging
from collections.abc import AsyncIterator
from time import perf_counter
from typing import Any

import dashscope
from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult
from dashscope.audio.http_tts.http_speech_synthesizer import HttpSpeechSynthesizer

from voice_shopping_api.core.config import get_settings
from voice_shopping_api.core.observability import finish_trace, start_trace
from voice_shopping_api.core.text import split_sentences, take_completed_sentences

logger = logging.getLogger(__name__)

__all__ = ["StreamingAsr", "split_sentences", "synthesize_chunks"]


class _AsrCallback(RecognitionCallback):
    def __init__(self, owner: "StreamingAsr") -> None:
        self.owner = owner

    def on_complete(self) -> None:
        return

    def on_error(self, result: RecognitionResult) -> None:
        self.owner.request_id = result.get_request_id() or self.owner.request_id
        self.owner.error = result.message

    def on_event(self, result: RecognitionResult) -> None:
        self.owner.request_id = result.get_request_id() or self.owner.request_id
        sentence = result.get_sentence()
        if not isinstance(sentence, dict):
            return
        transcript = str(sentence.get("text") or "").strip()
        if not transcript:
            return
        self.owner.latest = transcript
        sentence_final = RecognitionResult.is_sentence_end(sentence)
        self.owner.accept_transcript(transcript, sentence_final=sentence_final)
        if sentence_final:
            self.owner.record_usage(result.get_usage(sentence))


class StreamingAsr:
    def __init__(self, session_id: str | None = None, turn_id: str | None = None) -> None:
        settings = get_settings()
        dashscope.api_key = settings.dashscope_api_key
        self.session_id = session_id
        self.turn_id = turn_id
        self.latest = ""
        self.completed: list[str] = []
        self._pending_transcript = ""
        self._latest_hypothesis = ""
        self.error = ""
        self.received_bytes = 0
        self.request_id = ""
        self.usage: dict[str, Any] | None = None
        self._loop = asyncio.get_running_loop()
        self._completed_sentences: asyncio.Queue[str | None] = asyncio.Queue()
        self._sentences_closed = False
        self._trace_started = perf_counter()
        self._trace_closed = False
        trace_metadata = {
            "ls_provider": "dashscope",
            "ls_model_name": settings.asr_model,
            "operation": "asr",
            "channel": "audio",
            "sample_rate": 16000,
            "session_id": session_id,
            "turn_id": turn_id,
        }
        self._trace = start_trace(
            "dashscope-asr",
            run_type="llm",
            inputs={
                "format": "pcm",
                "sample_rate": 16000,
                "session_id": session_id,
                "turn_id": turn_id,
            },
            metadata=trace_metadata,
            tags=["dashscope", "asr", "audio"],
            project_name=settings.langsmith_project,
        )
        try:
            self.recognition = Recognition(
                model=settings.asr_model,
                format="pcm",
                sample_rate=16000,
                callback=_AsrCallback(self),
            )
        except Exception as exc:
            self._finish_trace(error=exc)
            raise

    def _finish_trace(self, transcript: str = "", error: BaseException | None = None) -> None:
        if self._trace_closed:
            return
        self._trace_closed = True
        trace_transcript = transcript or "".join(self.completed) or self.latest
        metadata: dict[str, Any] = {
            "status": "error" if error is not None else "ok",
            "duration_ms": round((perf_counter() - self._trace_started) * 1000, 2),
            "audio_bytes": self.received_bytes,
            "sentence_count": len(self.completed),
            "transcript": trace_transcript,
            "transcript_length": len(trace_transcript),
            "completed_sentences": list(self.completed),
        }
        if self.request_id:
            metadata["request_id"] = self.request_id
        if self.usage:
            metadata["dashscope_usage"] = dict(self.usage)
        if self.usage and self.usage.get("duration") is not None:
            metadata["billed_audio_duration_ms"] = self.usage["duration"]
        finish_trace(
            self._trace,
            outputs=None if error is not None else {
                "transcript": trace_transcript,
                "transcript_length": len(trace_transcript),
                "sentence_count": len(self.completed),
            },
            metadata=metadata,
            error=error,
        )

    def add_completed_sentence(self, transcript: str) -> None:
        """Receive a sentence-final ASR result from DashScope's worker thread."""
        transcript = transcript.strip()
        if not transcript or (self.completed and self.completed[-1] == transcript):
            return
        self.completed.append(transcript)
        self._loop.call_soon_threadsafe(self._completed_sentences.put_nowait, transcript)

    def accept_transcript(self, transcript: str, *, sentence_final: bool) -> None:
        """Split ASR hypotheses at punctuation and emit each segment once."""
        previous = self._latest_hypothesis
        if transcript.startswith(previous):
            delta = transcript[len(previous) :]
        elif previous.startswith(transcript):
            delta = ""
        else:
            delta = transcript
        self._pending_transcript += delta
        completed, self._pending_transcript = take_completed_sentences(self._pending_transcript)
        for item in completed:
            self.add_completed_sentence(item)
        if sentence_final:
            remainder = self._pending_transcript.strip()
            if remainder:
                self.add_completed_sentence(remainder)
            self._pending_transcript = ""
            self._latest_hypothesis = ""
        else:
            self._latest_hypothesis = transcript

    def record_usage(self, usage: dict[str, Any] | None) -> None:
        """Accumulate per-sentence ASR billing units for the whole turn."""
        if not usage:
            return
        if self.usage is None:
            self.usage = dict(usage)
            return
        for key, value in usage.items():
            current = self.usage.get(key)
            if isinstance(current, (int, float)) and isinstance(value, (int, float)):
                self.usage[key] = current + value
            else:
                self.usage[key] = value

    async def next_completed_sentence(self) -> str | None:
        """Wait for the next sentence-final result, or ``None`` once ASR stops."""
        return await self._completed_sentences.get()

    def _close_completed_sentences(self) -> None:
        if self._sentences_closed:
            return
        self._sentences_closed = True
        self._loop.call_soon_threadsafe(self._completed_sentences.put_nowait, None)

    async def start(self) -> None:
        try:
            await asyncio.to_thread(self.recognition.start)
        except Exception as exc:
            self._finish_trace(error=exc)
            raise

    async def send(self, audio: bytes) -> None:
        try:
            await asyncio.to_thread(self.recognition.send_audio_frame, audio)
            self.received_bytes += len(audio)
        except Exception as exc:
            self._finish_trace(error=exc)
            raise

    async def stop(self) -> str:
        try:
            await asyncio.to_thread(self.recognition.stop)
            if self.error:
                raise RuntimeError(self.error)
            remainder = self._pending_transcript.strip()
            if remainder:
                self.add_completed_sentence(remainder)
            self._pending_transcript = ""
            transcript = "".join(self.completed) or self.latest
            self._finish_trace(transcript=transcript)
            return transcript
        except Exception as exc:
            self._finish_trace(error=exc)
            raise
        finally:
            self._close_completed_sentences()


async def synthesize_chunks(
    text_value: str,
    *,
    session_id: str | None = None,
    turn_id: str | None = None,
) -> AsyncIterator[bytes]:
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
    }
    trace_metadata["session_id"] = session_id
    trace_metadata["turn_id"] = turn_id
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
            # DashScope TTS bills characters; the streaming result does not expose
            # a final response usage object, so retain the safe count as metadata.
            "usage_characters": len(text_value),
        }
        if request_id:
            metadata["request_id"] = request_id
        if provider_usage:
            metadata["dashscope_usage"] = dict(provider_usage)
        finish_trace(
            span,
            outputs=None if error is not None else {
                "chunk_count": chunk_count,
                "audio_bytes": audio_bytes,
            },
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
