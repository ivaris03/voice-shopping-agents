import pytest

from voice_shopping_api.realtime import speech


class _FakeRecognition:
    def __init__(self, **kwargs) -> None:
        self.callback = kwargs["callback"]

    def start(self) -> None:
        return

    def send_audio_frame(self, audio: bytes) -> None:
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
async def test_asr_trace_records_safe_usage_summary(monkeypatch) -> None:
    started: list[tuple[str, dict]] = []
    finished: list[dict] = []

    def fake_start(name: str, **kwargs):
        started.append((name, kwargs))
        return object()

    def fake_finish(handle, **kwargs) -> None:
        finished.append(kwargs)

    monkeypatch.setattr(speech, "start_trace", fake_start)
    monkeypatch.setattr(speech, "finish_trace", fake_finish)
    monkeypatch.setattr(speech, "Recognition", _FakeRecognition)

    asr = speech.StreamingAsr(session_id="session-1", turn_id="turn-1")
    await asr.start()
    await asr.send(b"\x00" * 4)
    asr.add_completed_sentence("不要上传这句原文")
    asr.record_usage({"duration": 100})
    asr.record_usage({"duration": 25})

    assert await asr.stop() == "不要上传这句原文"
    assert started[0][0] == "dashscope-asr"
    assert finished[0]["metadata"]["audio_bytes"] == 4
    assert finished[0]["metadata"]["billed_audio_duration_ms"] == 125
    assert "不要上传这句原文" not in str(finished[0])


@pytest.mark.asyncio
async def test_tts_stream_uses_dashscope_audio_format(monkeypatch) -> None:
    calls: dict[str, object] = {}
    started: list[tuple[str, dict]] = []
    finished: list[dict] = []

    class _FakeSynthesizer:
        @classmethod
        def call(cls, **kwargs):
            calls.update(kwargs)
            return iter([_FakeTtsChunk(audio_data=b"wav-1"), _FakeTtsChunk(audio_data=b"wav-2")])

    monkeypatch.setattr(speech, "HttpSpeechSynthesizer", _FakeSynthesizer)
    monkeypatch.setattr(
        speech,
        "start_trace",
        lambda name, **kwargs: started.append((name, kwargs)) or object(),
    )
    monkeypatch.setattr(speech, "finish_trace", lambda handle, **kwargs: finished.append(kwargs))
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
                "langsmith_project": "test-project",
            },
        )(),
    )

    chunks = [chunk async for chunk in speech.synthesize_chunks("你好")]

    assert chunks == [b"wav-1", b"wav-2"]
    assert calls["audio_format"] == "wav"
    assert calls["stream"] is True
    assert started[0][0] == "dashscope-tts"
    assert finished[0]["metadata"]["usage_characters"] == 2
    assert finished[0]["metadata"]["audio_bytes"] == len(b"wav-1wav-2")
