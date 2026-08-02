import pytest

from voice_shopping_api.realtime import speech


class _FakeRecognition:
    def __init__(self, **kwargs) -> None:
        self.callback = kwargs["callback"]

    def start(self) -> None:
        return

    def stop(self) -> None:
        return


@pytest.mark.asyncio
async def test_streaming_asr_exposes_sentence_final_results(monkeypatch) -> None:
    monkeypatch.setattr(speech, "Recognition", _FakeRecognition)
    asr = speech.StreamingAsr()

    asr.add_completed_sentence("第一句。")
    assert await asr.next_completed_sentence() == "第一句。"

    assert await asr.stop() == "第一句。"
    assert await asr.next_completed_sentence() is None
