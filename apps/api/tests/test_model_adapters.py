from typing import Any

import pytest

from voice_shopping_api.agents import model as model_module
from voice_shopping_api.core import embeddings as embeddings_module
from voice_shopping_api.core import observability as observability_module


class _FakeChatModel:
    init_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        type(self).init_kwargs = kwargs

    async def ainvoke(self, messages: list[tuple[str, str]]) -> Any:
        assert messages[0][0] == "system"
        assert messages[1][0] == "human"
        return type(
            "Message",
            (),
            {
                "content": '{"intent":{"type":"CHAT","confidence":0.99}}',
                "usage_metadata": {"input_tokens": 4, "output_tokens": 5, "total_tokens": 9},
            },
        )()

    async def astream(self, messages: list[tuple[str, str]]) -> Any:
        assert messages[0][0] == "system"
        assert messages[1][0] == "human"
        for content in ['{"speech_text":"正在', '筛选。","reasons":[]}']:
            yield type("MessageChunk", (), {"content": content, "usage_metadata": None})()
        yield type(
            "UsageChunk",
            (),
            {
                "content": "",
                "usage_metadata": {"input_tokens": 4, "output_tokens": 5, "total_tokens": 9},
            },
        )()


class _FakeEmbeddings:
    init_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        type(self).init_kwargs = kwargs

    def embed_query(self, text: str) -> list[float]:
        assert text == "通勤耳机"
        return [3.0, 4.0]


class _FakeTextEmbedding:
    @classmethod
    def call(cls, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["text_type"] == "query"
        return {
            "usage": {"input_tokens": 7},
            "output": {"embeddings": [{"embedding": [3.0, 4.0]}]},
        }


class _FakeEmbeddingsWithUsage:
    def __init__(self, **kwargs: Any) -> None:
        self.client: Any = None

    def embed_query(self, text: str) -> list[float]:
        response = self.client.call(input=text, text_type="query", model="test")
        return response["output"]["embeddings"][0]["embedding"]


class _FakeReranker:
    init_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        type(self).init_kwargs = kwargs

    def rerank(self, documents: list[str], query: str, *, top_n: int) -> list[dict[str, Any]]:
        assert query == "通勤耳机"
        assert len(documents) == 2
        assert top_n == 2
        return [
            {"index": 1, "relevance_score": 0.8},
            {"index": 0, "relevance_score": 0.4},
        ]


@pytest.mark.asyncio
async def test_chat_json_uses_chat_qwen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_module, "ChatQwen", _FakeChatModel)

    result = await model_module._chat_json("system prompt", {"utterance": "你好"})

    assert result == {"intent": {"type": "CHAT", "confidence": 0.99}}
    assert _FakeChatModel.init_kwargs["model"] == "qwen3.7-flash"
    assert _FakeChatModel.init_kwargs["enable_thinking"] is False
    assert _FakeChatModel.init_kwargs["model_kwargs"] == {
        "response_format": {"type": "json_object"}
    }


@pytest.mark.asyncio
async def test_streaming_chat_qwen_exposes_speech_text_deltas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(model_module, "ChatQwen", _FakeChatModel)
    deltas: list[str] = []

    async def collect(delta: str) -> None:
        deltas.append(delta)

    result = await model_module._stream_chat_json("system prompt", {}, collect)

    assert result == {"speech_text": "正在筛选。", "reasons": []}
    assert "".join(deltas) == "正在筛选。"


@pytest.mark.asyncio
async def test_embedding_uses_dashscope_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embeddings_module, "DashScopeEmbeddings", _FakeEmbeddings)

    vector, usage = await embeddings_module.embed_text("通勤耳机")

    assert vector == [3.0, 4.0]
    assert usage is None
    assert _FakeEmbeddings.init_kwargs["model"] == "qwen3.7-text-embedding"


@pytest.mark.asyncio
async def test_embedding_preserves_dashscope_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embeddings_module, "DashScopeEmbeddings", _FakeEmbeddingsWithUsage)
    monkeypatch.setattr(embeddings_module.dashscope, "TextEmbedding", _FakeTextEmbedding)

    vector, usage = await embeddings_module.embed_text("通勤耳机")

    assert vector == [3.0, 4.0]
    assert usage == {"input_tokens": 7, "total_tokens": 7}


def test_usage_normalization_preserves_provider_costs() -> None:
    assert observability_module.normalize_usage(
        {"prompt_tokens": 4, "completion_tokens": 5, "input_cost": "0.01"}
    ) == {
        "input_tokens": 4,
        "output_tokens": 5,
        "total_tokens": 9,
        "input_cost": 0.01,
        "total_cost": 0.01,
    }


@pytest.mark.asyncio
async def test_rerank_uses_dashscope_rerank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_module, "DashScopeRerank", _FakeReranker)
    products = [
        {"id": "product-1", "name": "商品一"},
        {"id": "product-2", "name": "商品二"},
    ]

    result = await model_module.rerank_products("通勤耳机", products)

    assert result == {"product-1": 0.4, "product-2": 0.8}
    assert _FakeReranker.init_kwargs["model"] == "qwen3-rerank"
    assert _FakeReranker.init_kwargs["dashscope_api_key"]
    assert isinstance(_FakeReranker.init_kwargs["client"], model_module._InstructionalRerankClient)
