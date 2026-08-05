import asyncio

import pytest

from voice_shopping_api.realtime import asr, speech, tts
from voice_shopping_api.realtime import hub as realtime_hub


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

    monkeypatch.setattr(realtime_hub, "synthesize_chunks", fake_synthesize_chunks)
    connection = _FakeAudioConnection()
    hub = realtime_hub.RealtimeHub.__new__(realtime_hub.RealtimeHub)
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
async def test_run_turn_forwards_streamed_sentence_metadata_to_audio_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentence_calls: list[tuple[object, ...]] = []
    done_calls: list[tuple[object, ...]] = []

    class _SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args) -> None:
            return None

    async def fake_process_turn(*_args, **kwargs):
        await kwargs["on_speech_sentence"]("第一句。", 1, 2)
        await kwargs["on_speech_sentence"]("第二句！", 2, 2)
        return {"speech_audio_streamed": True}, []

    async def fake_publish_audio_sentence(*args, **kwargs) -> None:
        sentence_calls.append((*args, kwargs.get("user_id")))

    async def fake_publish_audio_done(*args, **kwargs) -> None:
        done_calls.append(args)

    monkeypatch.setattr(realtime_hub, "async_session_factory", lambda: _SessionContext())
    monkeypatch.setattr(realtime_hub, "process_turn", fake_process_turn)
    hub = realtime_hub.RealtimeHub.__new__(realtime_hub.RealtimeHub)
    hub.locks = {"session-1": asyncio.Lock()}
    hub.publish_audio_sentence = fake_publish_audio_sentence
    hub.publish_audio_done = fake_publish_audio_done

    await hub.run_turn("session-1", "turn-1", "你好", "user-1")

    assert sentence_calls == [
        ("session-1", "turn-1", "第一句。", 1, 2, "user-1"),
        ("session-1", "turn-1", "第二句！", 2, 2, "user-1"),
    ]
    assert done_calls == [("session-1", "turn-1", 2, "user-1")]


@pytest.mark.asyncio
async def test_streaming_asr_exposes_sentence_final_results(monkeypatch) -> None:
    monkeypatch.setattr(asr, "Recognition", _FakeRecognition)
    streaming_asr = asr.StreamingAsr()

    streaming_asr.add_completed_sentence("第一句。")
    assert await streaming_asr.next_completed_sentence() == "第一句。"

    assert await streaming_asr.stop() == "第一句。"
    assert await streaming_asr.next_completed_sentence() is None


@pytest.mark.asyncio
async def test_asr_splits_punctuation_and_flushes_only_the_unfinished_tail(monkeypatch) -> None:
    monkeypatch.setattr(asr, "Recognition", _FakeRecognition)
    streaming_asr = asr.StreamingAsr()

    streaming_asr.accept_transcript("我想买一双，", sentence_final=False)
    streaming_asr.accept_transcript("我想买一双，通勤鞋？", sentence_final=True)
    streaming_asr.accept_transcript("预算五百", sentence_final=False)

    assert await streaming_asr.next_completed_sentence() == "我想买一双，"
    assert await streaming_asr.next_completed_sentence() == "通勤鞋？"
    assert await streaming_asr.stop() == "我想买一双，通勤鞋？预算五百"
    assert await streaming_asr.next_completed_sentence() == "预算五百"
    assert await streaming_asr.next_completed_sentence() is None


def test_asr_prefers_the_explicit_sentence_end_flag() -> None:
    assert asr._is_sentence_final({"sentence_end": True, "end_time": None})
    assert not asr._is_sentence_final({"sentence_end": False, "end_time": 123})
    assert asr._is_sentence_final({"end_time": 123})


@pytest.mark.asyncio
async def test_asr_streams_the_full_partial_transcript_before_finalizing(monkeypatch) -> None:
    monkeypatch.setattr(asr, "Recognition", _FakeRecognition)
    streaming_asr = asr.StreamingAsr()

    streaming_asr.accept_transcript(
        "我想买一副通勤耳机",
        sentence_final=False,
        sentence_id=1,
    )
    assert await streaming_asr.next_transcript_update() == (
        "partial",
        "我想买一副通勤耳机",
        "我想买一副通勤耳机",
    )

    streaming_asr.accept_transcript(
        "我想买一副通勤耳机，预算一千元以内。",
        sentence_final=True,
        sentence_id=1,
    )
    assert await streaming_asr.next_transcript_update() == (
        "sentence",
        "我想买一副通勤耳机，预算一千元以内。",
        "我想买一副通勤耳机，预算一千元以内。",
    )
    assert await streaming_asr.stop() == "我想买一副通勤耳机，预算一千元以内。"
    assert await streaming_asr.next_transcript_update() is None


@pytest.mark.asyncio
async def test_asr_keeps_identical_final_sentences_with_different_sentence_ids(monkeypatch) -> None:
    monkeypatch.setattr(asr, "Recognition", _FakeRecognition)
    streaming_asr = asr.StreamingAsr()

    streaming_asr.accept_transcript("好的。", sentence_final=True, sentence_id=1)
    streaming_asr.accept_transcript("好的。", sentence_final=True, sentence_id=2)

    assert await streaming_asr.stop() == "好的。好的。"


@pytest.mark.asyncio
async def test_asr_trace_records_safe_usage_summary(monkeypatch) -> None:
    started: list[tuple[str, dict]] = []
    finished: list[dict] = []

    def fake_start(name: str, **kwargs):
        started.append((name, kwargs))
        return object()

    def fake_finish(handle, **kwargs) -> None:
        finished.append(kwargs)

    monkeypatch.setattr(asr, "start_trace", fake_start)
    monkeypatch.setattr(asr, "finish_trace", fake_finish)
    monkeypatch.setattr(asr, "Recognition", _FakeRecognition)

    streaming_asr = asr.StreamingAsr(session_id="session-1", turn_id="turn-1")
    await streaming_asr.start()
    await streaming_asr.send(b"\x00" * 4)
    streaming_asr.add_completed_sentence("不要上传这句原文")
    streaming_asr.record_usage({"duration": 100})
    streaming_asr.record_usage({"duration": 25})

    assert await streaming_asr.stop() == "不要上传这句原文"
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

    monkeypatch.setattr(tts, "HttpSpeechSynthesizer", _FakeSynthesizer)
    monkeypatch.setattr(
        tts,
        "start_trace",
        lambda name, **kwargs: started.append((name, kwargs)) or object(),
    )
    monkeypatch.setattr(tts, "finish_trace", lambda handle, **kwargs: finished.append(kwargs))
    monkeypatch.setattr(
        tts,
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

    chunks = [chunk async for chunk in tts.synthesize_chunks("你好")]

    assert chunks == [b"wav-1", b"wav-2"]
    assert calls["audio_format"] == "wav"
    assert calls["stream"] is True
    assert started[0][0] == "dashscope-tts"
    assert finished[0]["metadata"]["usage_characters"] == 2
    assert finished[0]["metadata"]["audio_bytes"] == len(b"wav-1wav-2")
    assert finished[0]["metadata"]["text"] == "你好"
    assert finished[0]["outputs"]["audio_bytes"] == len(b"wav-1wav-2")
