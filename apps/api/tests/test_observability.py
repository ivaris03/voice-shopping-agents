from typing import Any

from voice_shopping_api.core import observability


class _FakeRun:
    def __init__(self) -> None:
        self.metadata: dict[str, Any] = {}
        self.set_kwargs: dict[str, Any] = {}
        self.end_kwargs: dict[str, Any] = {}

    def set(self, **kwargs: Any) -> None:
        self.set_kwargs.update(kwargs)

    def add_metadata(self, metadata: dict[str, Any]) -> None:
        self.metadata.update(metadata)

    def end(self, **kwargs: Any) -> None:
        self.end_kwargs.update(kwargs)


class _FakeContext:
    def __init__(self, run: _FakeRun) -> None:
        self.run = run
        self.exit_args: tuple[Any, ...] | None = None

    def __enter__(self) -> _FakeRun:
        return self.run

    def __exit__(self, *args: Any) -> None:
        self.exit_args = args


def test_start_trace_is_noop_when_tracing_is_disabled(monkeypatch) -> None:
    called = False

    def fake_trace(*args: Any, **kwargs: Any) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(observability, "tracing_is_enabled", lambda: False)
    monkeypatch.setattr(observability, "trace", fake_trace)

    assert observability.start_trace("disabled") is None
    assert called is False


def test_start_and_finish_trace_preserve_run_fields(monkeypatch) -> None:
    run = _FakeRun()
    context = _FakeContext(run)
    trace_args: dict[str, Any] = {}

    def fake_trace(name: str, **kwargs: Any) -> _FakeContext:
        trace_args.update(name=name, **kwargs)
        return context

    monkeypatch.setattr(observability, "tracing_is_enabled", lambda: True)
    monkeypatch.setattr(observability, "trace", fake_trace)

    handle = observability.start_trace(
        "dashscope-test",
        run_type="retriever",
        inputs={"query": "通勤耳机"},
        metadata={"ls_model_name": "qwen3-rerank"},
        tags=["dashscope", "test"],
        project_name="test-project",
    )
    assert handle is not None
    assert trace_args["name"] == "dashscope-test"
    assert trace_args["inputs"] == {"query": "通勤耳机"}
    assert trace_args["metadata"] == {"ls_model_name": "qwen3-rerank"}

    observability.finish_trace(
        handle,
        outputs={"scores": {"product-1": 0.9}},
        metadata={"duration_ms": 12.5},
        usage={
            "prompt_tokens": 4,
            "completion_tokens": 5,
            "input_cost": "0.01",
        },
    )

    assert run.set_kwargs["usage_metadata"] == {
        "input_tokens": 4,
        "output_tokens": 5,
        "total_tokens": 9,
        "input_cost": 0.01,
        "total_cost": 0.01,
    }
    assert run.metadata["duration_ms"] == 12.5
    assert run.metadata["usage"]["total_tokens"] == 9
    assert run.metadata["cost"] == 0.01
    assert run.end_kwargs == {"outputs": {"scores": {"product-1": 0.9}}}
    assert context.exit_args == (None, None, None)


def test_finish_trace_keeps_raw_error_and_always_closes_context(monkeypatch) -> None:
    run = _FakeRun()
    context = _FakeContext(run)
    monkeypatch.setattr(observability, "tracing_is_enabled", lambda: True)
    monkeypatch.setattr(observability, "trace", lambda *args, **kwargs: context)

    handle = observability.start_trace("error-test")
    assert handle is not None
    error = RuntimeError("DashScope request failed: request-id-123")

    observability.finish_trace(handle, metadata={"status": "error"}, error=error)

    assert run.metadata["status"] == "error"
    assert run.metadata["error"] == str(error)
    assert run.end_kwargs == {"error": str(error)}
    assert context.exit_args == (None, None, None)


def test_trace_failures_are_fail_open(monkeypatch) -> None:
    class _BrokenRun(_FakeRun):
        def add_metadata(self, metadata: dict[str, Any]) -> None:
            raise RuntimeError("telemetry unavailable")

    run = _BrokenRun()
    context = _FakeContext(run)
    monkeypatch.setattr(observability, "tracing_is_enabled", lambda: True)
    monkeypatch.setattr(observability, "trace", lambda *args, **kwargs: context)

    handle = observability.start_trace("broken-test")
    assert handle is not None
    observability.finish_trace(handle, metadata={"status": "ok"})

    assert context.exit_args == (None, None, None)
