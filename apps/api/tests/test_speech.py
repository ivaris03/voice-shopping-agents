import pytest

from voice_shopping_api.realtime import router as realtime_router
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


class _FakeAudioConnection:
    def __init__(self) -> None:
        self.messages: list[tuple[str, object]] = []

    async def send_json(self, message: object) -> None:
        self.messages.append(("json", message))

    async def send_bytes(self, message: bytes) -> None:
        self.messages.append(("bytes", message))


def test_split_sentences_preserves_sentence_punctuation() -> None:
    assert speech.split_sentences("第一句。第二句！第三句？") == [
        "第一句。",
        "第二句！",
        "第三句？",
    ]


@pytest.mark.asyncio
async def test_publish_audio_synthesizes_and_pushes_each_sentence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_synthesize_chunks(text_value: str, **kwargs):
        calls.append(text_value)
        yield f"audio:{text_value}".encode()

    monkeypatch.setattr(realtime_router, "synthesize_chunks", fake_synthesize_chunks)
    connection = _FakeAudioConnection()
    hub = realtime_router.RealtimeHub.__new__(realtime_router.RealtimeHub)
    hub.audio_connections = {"session-1": {connection}}

    await hub.publish_audio("session-1", "turn-1", "第一句。第二句！")

    assert calls == ["第一句。", "第二句！"]
    assert [kind for kind, _ in connection.messages] == [
        "json",
        "bytes",
        "json",
        "json",
        "bytes",
        "json",
    ]
    starts = [
        message
        for kind, message in connection.messages
        if kind == "json" and message["type"] == "audio.start"
    ]
    ends = [
        message
        for kind, message in connection.messages
        if kind == "json" and message["type"] == "audio.end"
    ]
    assert [message["payload"]["sentenceIndex"] for message in starts] == [1, 2]
    assert [message["payload"]["sentenceIndex"] for message in ends] == [1, 2]
    assert starts[0]["payload"]["sentenceCount"] == 2
    assert ends[-1]["payload"]["final"] is True


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
    assert finished[0]["metadata"]["transcript"] == "不要上传这句原文"
    assert finished[0]["outputs"]["transcript"] == "不要上传这句原文"


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
    assert finished[0]["metadata"]["text"] == "你好"
    assert finished[0]["outputs"]["audio_bytes"] == len(b"wav-1wav-2")
