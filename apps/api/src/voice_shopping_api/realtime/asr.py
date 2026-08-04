"""DashScope streaming ASR adapter."""

import asyncio
import logging
from time import perf_counter
from typing import Any

import dashscope
from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult

from voice_shopping_api.core.config import get_settings
from voice_shopping_api.core.observability import finish_trace, start_trace
from voice_shopping_api.core.text import take_completed_sentences

logger = logging.getLogger(__name__)

__all__ = ["StreamingAsr"]


def _is_sentence_final(sentence: dict[str, Any]) -> bool:
    """Prefer the provider's explicit final-result flag when it is present."""
    sentence_end = sentence.get("sentence_end")
    if isinstance(sentence_end, bool):
        return sentence_end
    return RecognitionResult.is_sentence_end(sentence)


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
        if sentence.get("heartbeat") is True:
            return
        transcript = str(sentence.get("text") or "").strip()
        if not transcript:
            return
        self.owner.latest = transcript
        sentence_final = _is_sentence_final(sentence)
        self.owner.accept_transcript(
            transcript,
            sentence_final=sentence_final,
            sentence_id=sentence.get("sentence_id"),
        )
        if sentence_final:
            self.owner.record_usage(result.get_usage(sentence))


class StreamingAsr:
    """Bridge the callback-based ASR SDK to an asyncio sentence stream."""

    def __init__(self, session_id: str | None = None, turn_id: str | None = None) -> None:
        settings = get_settings()
        dashscope.api_key = settings.dashscope_api_key
        self.session_id = session_id
        self.turn_id = turn_id
        self.latest = ""
        self.completed: list[str] = []
        self._pending_transcript = ""
        self._latest_hypothesis = ""
        self._current_sentence = ""
        self._current_sentence_id: int | None = None
        self._final_sentence_ids: set[int] = set()
        self._last_partial_transcript = ""
        self.error = ""
        self.received_bytes = 0
        self.request_id = ""
        self.usage: dict[str, Any] | None = None
        self._loop = asyncio.get_running_loop()
        self._completed_sentences: asyncio.Queue[str | None] = asyncio.Queue()
        self._sentences_closed = False
        self._transcript_updates: asyncio.Queue[tuple[str, str, str] | None] = asyncio.Queue()
        self._updates_closed = False
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
        trace_transcript = transcript or self._transcript_so_far() or self.latest
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
            outputs=None
            if error is not None
            else {
                "transcript": trace_transcript,
                "transcript_length": len(trace_transcript),
                "sentence_count": len(self.completed),
            },
            metadata=metadata,
            error=error,
        )

    def add_completed_sentence(self, transcript: str, *, allow_duplicate: bool = False) -> bool:
        """Receive a sentence-final ASR result from DashScope's worker thread."""
        transcript = transcript.strip()
        if not transcript or (
            not allow_duplicate and self.completed and self.completed[-1] == transcript
        ):
            return False
        self.completed.append(transcript)
        self._loop.call_soon_threadsafe(self._completed_sentences.put_nowait, transcript)
        return True

    def _transcript_so_far(self) -> str:
        current = self._current_sentence or self._pending_transcript
        return f"{''.join(self.completed)}{current}".strip()

    def _publish_transcript_update(self, kind: str, transcript: str, full_transcript: str) -> None:
        self._loop.call_soon_threadsafe(
            self._transcript_updates.put_nowait,
            (kind, transcript, full_transcript),
        )

    def _publish_partial_transcript(self) -> None:
        transcript = self._transcript_so_far()
        if not transcript or transcript == self._last_partial_transcript:
            return
        self._last_partial_transcript = transcript
        self._publish_transcript_update("partial", transcript, transcript)

    def _publish_completed_sentences(self, sentences: list[str]) -> None:
        if not sentences:
            return
        full_transcript = self._transcript_so_far()
        for sentence in sentences:
            self._publish_transcript_update("sentence", sentence, full_transcript)

    @staticmethod
    def _sentence_id(value: Any) -> int | None:
        if isinstance(value, bool) or value is None:
            return None
        try:
            sentence_id = int(value)
        except (TypeError, ValueError):
            return None
        return sentence_id if sentence_id > 0 else None

    def accept_transcript(
        self,
        transcript: str,
        *,
        sentence_final: bool,
        sentence_id: Any = None,
    ) -> None:
        """Accept a provider hypothesis and emit complete transcript updates."""
        normalized_sentence_id = self._sentence_id(sentence_id)
        if normalized_sentence_id is not None:
            self._accept_identified_sentence(
                transcript,
                sentence_final=sentence_final,
                sentence_id=normalized_sentence_id,
            )
            return

        previous = self._latest_hypothesis
        if transcript.startswith(previous):
            delta = transcript[len(previous) :]
        elif previous.startswith(transcript):
            delta = ""
        else:
            delta = transcript
        self._pending_transcript += delta
        completed, self._pending_transcript = take_completed_sentences(self._pending_transcript)
        new_sentences = [item for item in completed if self.add_completed_sentence(item)]
        if sentence_final:
            remainder = self._pending_transcript.strip()
            self._pending_transcript = ""
            self._latest_hypothesis = ""
            if remainder and self.add_completed_sentence(remainder):
                new_sentences.append(remainder)
        else:
            self._latest_hypothesis = transcript
        self._publish_completed_sentences(new_sentences)
        if not sentence_final:
            self._publish_partial_transcript()

    def _accept_identified_sentence(
        self, transcript: str, *, sentence_final: bool, sentence_id: int
    ) -> None:
        """Keep provider sentence revisions separate from already-final sentences."""
        self._pending_transcript = ""
        if sentence_final:
            if self._current_sentence_id == sentence_id:
                self._current_sentence = ""
                self._current_sentence_id = None
            self._latest_hypothesis = ""
            if sentence_id in self._final_sentence_ids:
                return
            self._final_sentence_ids.add(sentence_id)
            if self.add_completed_sentence(transcript, allow_duplicate=True):
                self._publish_completed_sentences([transcript])
            return

        if sentence_id in self._final_sentence_ids:
            return
        self._current_sentence_id = sentence_id
        self._current_sentence = transcript
        self._pending_transcript = ""
        self._latest_hypothesis = transcript
        self._publish_partial_transcript()

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

    async def next_transcript_update(self) -> tuple[str, str, str] | None:
        """Wait for the next full partial or sentence-final transcript update."""
        return await self._transcript_updates.get()

    def _close_completed_sentences(self) -> None:
        if self._sentences_closed:
            return
        self._sentences_closed = True
        self._loop.call_soon_threadsafe(self._completed_sentences.put_nowait, None)

    def _close_transcript_updates(self) -> None:
        if self._updates_closed:
            return
        self._updates_closed = True
        self._loop.call_soon_threadsafe(self._transcript_updates.put_nowait, None)

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
            active_sentence_id = self._current_sentence_id
            remainder = (self._current_sentence or self._pending_transcript).strip()
            self._current_sentence = ""
            self._current_sentence_id = None
            self._pending_transcript = ""
            if remainder and self.add_completed_sentence(
                remainder, allow_duplicate=active_sentence_id is not None
            ):
                self._publish_completed_sentences([remainder])
            transcript = "".join(self.completed) or self.latest
            self._finish_trace(transcript=transcript)
            return transcript
        except Exception as exc:
            self._finish_trace(error=exc)
            raise
        finally:
            self._close_completed_sentences()
            self._close_transcript_updates()
