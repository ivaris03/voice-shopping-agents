import sys
from types import SimpleNamespace

import pytest

from voice_shopping_api import server as server_module
from voice_shopping_api.agents import checkpointer as checkpointer_module


@pytest.mark.asyncio
async def test_checkpointer_falls_back_on_windows_proactor_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProactorLoop:
        pass

    saver = checkpointer_module.LazyPostgresCheckpointer()
    monkeypatch.setattr(checkpointer_module.sys, "platform", "win32")
    monkeypatch.setattr(
        checkpointer_module.asyncio,
        "ProactorEventLoop",
        FakeProactorLoop,
        raising=False,
    )
    monkeypatch.setattr(
        checkpointer_module.asyncio,
        "get_running_loop",
        lambda: FakeProactorLoop(),
    )
    monkeypatch.setattr(
        checkpointer_module,
        "get_settings",
        lambda: SimpleNamespace(langgraph_checkpoint_enabled=True),
    )

    assert await saver.get() is None
    assert await saver.get() is None
    await saver.close()


def test_server_uses_selector_loop_factory_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def run(*args: object, **kwargs: object) -> None:
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(server_module.sys, "platform", "win32")
    monkeypatch.setattr(server_module.sys, "argv", ["server.py"])
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=run))

    server_module.main()

    assert captured["args"] == ("voice_shopping_api.main:app",)
    assert captured["kwargs"] == {
        "host": "127.0.0.1",
        "port": 8000,
        "reload": False,
        "loop": "voice_shopping_api.server:windows_selector_loop_factory",
    }


def test_windows_selector_loop_factory_returns_selector_loop() -> None:
    loop = server_module.windows_selector_loop_factory()
    try:
        assert isinstance(loop, server_module.asyncio.SelectorEventLoop)
    finally:
        loop.close()
