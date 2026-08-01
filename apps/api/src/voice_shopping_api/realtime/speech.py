import asyncio
import logging
from collections.abc import AsyncIterator

import dashscope
from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult
from dashscope.audio.http_tts.http_speech_synthesizer import HttpSpeechSynthesizer

from voice_shopping_api.core.config import get_settings

logger = logging.getLogger(__name__)


class _AsrCallback(RecognitionCallback):
    def __init__(self, owner: "StreamingAsr") -> None:
        self.owner = owner

    def on_complete(self) -> None:
        return

    def on_error(self, result: RecognitionResult) -> None:
        self.owner.error = result.message

    def on_event(self, result: RecognitionResult) -> None:
        sentence = result.get_sentence()
        transcript = str(sentence.get("text") or "").strip()
        if not transcript:
            return
        self.owner.latest = transcript
        if RecognitionResult.is_sentence_end(sentence):
            self.owner.completed.append(transcript)


class StreamingAsr:
    def __init__(self) -> None:
        settings = get_settings()
        dashscope.api_key = settings.dashscope_api_key
        self.latest = ""
        self.completed: list[str] = []
        self.error = ""
        self.recognition = Recognition(
            model=settings.asr_model,
            format="pcm",
            sample_rate=16000,
            callback=_AsrCallback(self),
        )

    async def start(self) -> None:
        await asyncio.to_thread(self.recognition.start)

    async def send(self, audio: bytes) -> None:
        await asyncio.to_thread(self.recognition.send_audio_frame, audio)

    async def stop(self) -> str:
        await asyncio.to_thread(self.recognition.stop)
        if self.error:
            raise RuntimeError(self.error)
        return "".join(self.completed) or self.latest


async def synthesize_chunks(text_value: str) -> AsyncIterator[bytes]:
    settings = get_settings()
    if not settings.dashscope_api_key or not text_value:
        return
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    errors: list[Exception] = []
    loop = asyncio.get_running_loop()

    def generate() -> None:
        try:
            dashscope.api_key = settings.dashscope_api_key
            dashscope.base_http_api_url = settings.dashscope_http_base_url
            stream = HttpSpeechSynthesizer.call(
                model=settings.tts_model,
                text=text_value,
                voice=settings.tts_voice,
                format="wav",
                sample_rate=24000,
                stream=True,
                api_key=settings.dashscope_api_key,
            )
            for chunk in stream:
                if not chunk.audio_url and chunk.audio_data:
                    loop.call_soon_threadsafe(queue.put_nowait, bytes(chunk.audio_data))
        except Exception as exc:
            errors.append(exc)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    worker = asyncio.create_task(asyncio.to_thread(generate))
    while True:
        chunk = await queue.get()
        if chunk is None:
            break
        yield chunk
    await worker
    if errors:
        logger.warning("TTS failed; browser fallback will be used: %s", errors[0])
