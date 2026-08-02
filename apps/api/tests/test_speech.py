import pytest

from voice_shopping_api.realtime import speech


class _FakeRecognition:
    def __init__(self, **kwargs) -> None:
        self.callback = kwargs["callback"]

    def start(self) -> None:
        return

    def stop(self) -> None:
        return


class _FakeTtsChunk:
    def __init__(self, audio_data: bytes | None = None, audio_url: str | None = None) -> None:
        self.audio_data = audio_data
        self.audio_url = audio_url


@pytest.mark.asyncio
async def test_streaming_asr_exposes_sentence_final_results(monkeypatch) -> None:
    monkeypatch.setattr(speech, "Recognition", _FakeRecognition)
    asr = speech.StreamingAsr()

    asr.add_completed_sentence("第一句。")
    assert await asr.next_completed_sentence() == "第一句。"

    assert await asr.stop() == "第一句。"
    assert await asr.next_completed_sentence() is None


@pytest.mark.asyncio
async def test_tts_stream_uses_dashscope_audio_format(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class _FakeSynthesizer:
        @classmethod
        def call(cls, **kwargs):
            calls.update(kwargs)
            return iter([_FakeTtsChunk(audio_data=b"wav-1"), _FakeTtsChunk(audio_data=b"wav-2")])

    monkeypatch.setattr(speech, "HttpSpeechSynthesizer", _FakeSynthesizer)
    monkeypatch.setattr(
        speech,
        "get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "dashscope_api_key": "test-key",
                "dashscope_http_base_url": "https://example.test/api/v1",
                "tts_model": "qwen-audio-3.0-tts-plus",
                "tts_voice": "longanlingxin",
            },
        )(),
    )

    chunks = [chunk async for chunk in speech.synthesize_chunks("你好")]

    assert chunks == [b"wav-1", b"wav-2"]
    assert calls["audio_format"] == "wav"
    assert calls["stream"] is True
