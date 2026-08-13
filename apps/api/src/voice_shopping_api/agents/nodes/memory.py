import asyncio
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from langgraph.runtime import Runtime

from voice_shopping_api.agents.state import ShoppingRuntimeDependencies, ShoppingState

logger = logging.getLogger(__name__)


def _namespace(user_id: str, kind: str) -> tuple[str, ...]:
    return ("users", user_id, kind)


def model_memory_context(state: ShoppingState) -> list[str]:
    """Combine recent dialogue with recalled semantic facts for model calls."""
    history = list(state.get("conversation_history", []))
    memories = state.get("semantic_memories", [])
    memory_lines = [
        f"长期记忆: {memory['text']}"
        for memory in memories[:3]
        if isinstance(memory.get("text"), str)
    ]
    return [*history[-6:], *memory_lines]


def _profile_value(item: object) -> dict[str, Any]:
    value = getattr(item, "value", None)
    return dict(value) if isinstance(value, Mapping) else {}


def _memory_values(items: list[object]) -> list[dict[str, Any]]:
    return [value for item in items if (value := _profile_value(item))]


async def inject_profile_memory(
    state: ShoppingState,
    runtime: Runtime[ShoppingRuntimeDependencies],
) -> dict[str, Any]:
    """Load canonical and semantic user memory inside the graph execution."""
    user_id = state.get("user_id", "")
    context = runtime.context
    snapshot = dict(state.get("user_profile_snapshot", {}))
    if context and context.profile_loader:
        try:
            snapshot = await context.profile_loader()
        except Exception as exc:
            logger.warning("Canonical profile load failed; using supplied snapshot: %s", exc)

    semantic_memories: list[dict[str, Any]] = []
    store = runtime.store
    if store is not None and user_id:
        try:
            static_item, dynamic_item, memories = await asyncio.gather(
                store.aget(_namespace(user_id, "profile"), "static"),
                store.aget(_namespace(user_id, "profile"), "dynamic"),
                store.asearch(
                    _namespace(user_id, "semantic"),
                    query=state.get("utterance") if state.get("model_enabled") else None,
                    limit=5,
                ),
            )
            stored_profile = {
                "static": _profile_value(static_item),
                "dynamic": _profile_value(dynamic_item),
            }
            if not snapshot:
                snapshot = stored_profile
            else:
                # SQL is authoritative; Store fills only a missing profile layer.
                for layer in ("static", "dynamic"):
                    if not snapshot.get(layer) and stored_profile[layer]:
                        snapshot[layer] = stored_profile[layer]
            semantic_memories = _memory_values(list(memories))
        except Exception as exc:
            logger.warning("Long-term memory recall failed; continuing without it: %s", exc)

    return {
        "user_profile_snapshot": snapshot,
        "semantic_memories": semantic_memories,
    }


def _semantic_fact(state: ShoppingState) -> dict[str, Any] | None:
    category = state.get("product_category")
    slots = state.get("slots", {})
    utterance = state.get("utterance", "").strip()
    profile_updates = state.get("user_profile_updates", {})
    if not category and not slots and not profile_updates:
        return None
    facts = []
    if category:
        facts.append(f"品类={category}")
    if slots:
        facts.append(
            "偏好=" + "、".join(f"{key}:{value}" for key, value in sorted(slots.items()))
        )
    text_value = f"用户说：{utterance}"
    if facts:
        text_value += "；" + "；".join(facts)
    return {
        "text": text_value,
        "utterance": utterance,
        "productCategory": category,
        "slots": slots,
        "profileUpdates": profile_updates,
        "capturedAt": datetime.now(UTC).isoformat(),
    }


async def update_long_term_memory(
    state: ShoppingState,
    runtime: Runtime[ShoppingRuntimeDependencies],
) -> dict[str, Any]:
    """Asynchronously converge profile rows and persist this turn's semantic fact."""
    user_id = state.get("user_id", "")
    turn_id = state.get("turn_id", "")
    context = runtime.context
    snapshot = dict(state.get("user_profile_snapshot", {}))

    if context and context.profile_updater:
        try:
            snapshot = await context.profile_updater(state)
        except Exception as exc:
            logger.warning("Profile memory update failed; retaining the previous snapshot: %s", exc)

    store = runtime.store
    if store is not None and user_id and turn_id:
        operations = []
        semantic_fact = _semantic_fact(state)
        if semantic_fact is not None:
            operations.append(store.aput(
                _namespace(user_id, "semantic"),
                turn_id,
                semantic_fact,
            ))
        static_profile = snapshot.get("static")
        dynamic_profile = snapshot.get("dynamic")
        if isinstance(static_profile, Mapping):
            operations.append(
                store.aput(
                    _namespace(user_id, "profile"),
                    "static",
                    dict(static_profile),
                    index=False,
                )
            )
        if isinstance(dynamic_profile, Mapping):
            operations.append(
                store.aput(
                    _namespace(user_id, "profile"),
                    "dynamic",
                    dict(dynamic_profile),
                    index=False,
                )
            )
        results = await asyncio.gather(*operations, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                logger.warning("Long-term memory write failed: %s", result)

    return {"user_profile_snapshot": snapshot}
