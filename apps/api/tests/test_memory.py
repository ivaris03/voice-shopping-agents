from typing import Any

import pytest
from langgraph.runtime import Runtime
from langgraph.store.memory import InMemoryStore

from voice_shopping_api.agents.nodes.memory import (
    inject_profile_memory,
    model_memory_context,
    update_long_term_memory,
)
from voice_shopping_api.agents.state import ShoppingRuntimeDependencies


async def _catalog_loader(
    _query: str, _enabled: bool, _filters: dict[str, Any]
) -> list[dict[str, Any]]:
    return []


@pytest.mark.asyncio
async def test_memory_nodes_inject_and_update_user_scoped_memories() -> None:
    store = InMemoryStore()
    await store.aput(
        ("users", "user-1", "semantic"),
        "older-turn",
        {"text": "用户偏好机械表", "slots": {"movement": "automatic"}},
    )
    await store.aput(
        ("users", "another-user", "semantic"),
        "private-turn",
        {"text": "不应跨用户召回"},
    )
    updated_states: list[dict[str, Any]] = []

    async def load_profile() -> dict[str, Any]:
        return {
            "static": {"city": "上海"},
            "dynamic": {"brandAffinity": {"Citizen": 0.4}},
        }

    async def update_profile(state: dict[str, Any]) -> dict[str, Any]:
        updated_states.append(state)
        return {
            "static": {"city": "上海", "age": 30},
            "dynamic": {"brandAffinity": {"Citizen": 0.4}},
        }

    runtime = Runtime(
        context=ShoppingRuntimeDependencies(
            catalog_loader=_catalog_loader,
            profile_loader=load_profile,
            profile_updater=update_profile,
        ),
        store=store,
    )
    state = {
        "user_id": "user-1",
        "turn_id": "turn-2",
        "utterance": "想买一块光动能手表",
        "product_category": "WATCHES",
        "slots": {"movement": "eco-drive"},
        "model_enabled": False,
    }

    injected = await inject_profile_memory(state, runtime)

    assert injected["user_profile_snapshot"]["static"]["city"] == "上海"
    assert [memory["text"] for memory in injected["semantic_memories"]] == [
        "用户偏好机械表"
    ]

    updated = await update_long_term_memory({**state, **injected}, runtime)

    assert updated_states
    assert updated["user_profile_snapshot"]["static"]["age"] == 30
    semantic = await store.aget(("users", "user-1", "semantic"), "turn-2")
    static_profile = await store.aget(("users", "user-1", "profile"), "static")
    dynamic_profile = await store.aget(("users", "user-1", "profile"), "dynamic")
    assert semantic is not None
    assert semantic.value["productCategory"] == "WATCHES"
    assert semantic.value["slots"] == {"movement": "eco-drive"}
    assert static_profile is not None and static_profile.value["age"] == 30
    assert dynamic_profile is not None
    assert dynamic_profile.value["brandAffinity"] == {"Citizen": 0.4}


@pytest.mark.asyncio
async def test_memory_nodes_degrade_without_runtime_backends() -> None:
    runtime = Runtime()
    state = {
        "user_id": "user-1",
        "turn_id": "turn-1",
        "user_profile_snapshot": {"static": {"locale": "zh_cn"}},
    }

    injected = await inject_profile_memory(state, runtime)
    updated = await update_long_term_memory({**state, **injected}, runtime)

    assert injected == {
        "user_profile_snapshot": {"static": {"locale": "zh_cn"}},
        "semantic_memories": [],
    }
    assert updated == {"user_profile_snapshot": {"static": {"locale": "zh_cn"}}}


def test_model_memory_context_keeps_recalled_facts_separate_from_chat_history() -> None:
    context = model_memory_context(
        {
            "conversation_history": ["user: 你好", "assistant: 你好"],
            "semantic_memories": [
                {"text": "用户偏好轻量跑鞋"},
                {"ignored": "missing text"},
            ],
        }
    )

    assert context == [
        "user: 你好",
        "assistant: 你好",
        "长期记忆: 用户偏好轻量跑鞋",
    ]
