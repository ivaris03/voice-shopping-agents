"""Small, fail-open helpers for LangSmith spans and model usage."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from langsmith import trace
from langsmith.utils import tracing_is_enabled

logger = logging.getLogger(__name__)


@dataclass
class TraceHandle:
    """A manually managed LangSmith span used by long-lived or streaming calls."""

    context: Any
    run: Any


def start_trace(
    name: str,
    *,
    run_type: str = "chain",
    inputs: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    tags: list[str] | None = None,
    project_name: str | None = None,
) -> TraceHandle | None:
    """Start a span without making observability failures affect business calls."""
    try:
        if not tracing_is_enabled():
            return None
        context = trace(
            name,
            run_type=run_type,
            inputs=dict(inputs or {}),
            metadata=dict(metadata or {}),
            tags=tags,
            project_name=project_name,
        )
        return TraceHandle(context=context, run=context.__enter__())
    except Exception:  # noqa: BLE001 - tracing must never break the request path
        logger.debug("Unable to start LangSmith span %s", name, exc_info=True)
        return None


def finish_trace(
    handle: TraceHandle | None,
    *,
    outputs: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    usage: Any = None,
    error: BaseException | None = None,
) -> None:
    """Finish a span with debugging payloads and normalized usage metadata."""
    if handle is None:
        return
    try:
        normalized_usage = normalize_usage(usage)
        trace_metadata = dict(metadata or {})
        if normalized_usage:
            trace_metadata.setdefault("usage", normalized_usage)
            if normalized_usage.get("total_cost") is not None:
                trace_metadata.setdefault("cost", normalized_usage["total_cost"])
            handle.run.set(usage_metadata=normalized_usage)
        if error is not None:
            trace_metadata.setdefault("error", str(error) or type(error).__name__)
        if trace_metadata:
            handle.run.add_metadata(trace_metadata)
        if error is not None:
            handle.run.end(error=str(error) or type(error).__name__)
        else:
            handle.run.end(outputs=dict(outputs or {}))
    except Exception:  # noqa: BLE001 - tracing must never break the request path
        logger.debug("Unable to finish LangSmith span", exc_info=True)
    finally:
        try:
            handle.context.__exit__(None, None, None)
        except Exception:  # noqa: BLE001 - tracing must never break the request path
            logger.debug("Unable to flush LangSmith span", exc_info=True)


def normalize_usage(value: Any) -> dict[str, int | float] | None:
    """Convert provider usage objects to LangSmith's usage and cost shape."""
    if value is None:
        return None
    if isinstance(value, Mapping):
        raw = dict(value)
    elif hasattr(value, "model_dump") and callable(value.model_dump):
        raw = value.model_dump()
    else:
        try:
            raw = dict(value)
        except (TypeError, ValueError):
            return None

    token_aliases = {
        "input_tokens": ("input_tokens", "prompt_tokens", "inputTokens"),
        "output_tokens": ("output_tokens", "completion_tokens", "outputTokens"),
        "total_tokens": ("total_tokens", "totalTokens"),
    }
    cost_aliases = {
        "input_cost": ("input_cost", "prompt_cost", "inputCost"),
        "output_cost": ("output_cost", "completion_cost", "outputCost"),
        "total_cost": ("total_cost", "totalCost"),
    }
    normalized: dict[str, int | float] = {}
    for target, names in token_aliases.items():
        for name in names:
            if raw.get(name) is not None:
                try:
                    normalized[target] = int(raw[name])
                except (TypeError, ValueError):
                    continue
                else:
                    break
    for target, names in cost_aliases.items():
        for name in names:
            if raw.get(name) is not None:
                try:
                    normalized[target] = float(raw[name])
                except (TypeError, ValueError):
                    continue
                else:
                    break
    if "total_tokens" not in normalized:
        input_tokens = normalized.get("input_tokens")
        output_tokens = normalized.get("output_tokens")
        if input_tokens is not None or output_tokens is not None:
            normalized["total_tokens"] = (input_tokens or 0) + (output_tokens or 0)
    if "total_cost" not in normalized:
        input_cost = normalized.get("input_cost")
        output_cost = normalized.get("output_cost")
        if input_cost is not None or output_cost is not None:
            normalized["total_cost"] = (input_cost or 0) + (output_cost or 0)
    return normalized or None


def response_usage(response: Any) -> dict[str, int | float] | None:
    """Read usage from a DashScope response without retaining its payload."""
    if response is None:
        return None
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, Mapping):
        usage = response.get("usage")
    return normalize_usage(usage)


def response_request_id(response: Any) -> str | None:
    """Return a provider request id for correlation when the SDK exposes one."""
    if response is None:
        return None
    request_id = getattr(response, "request_id", None)
    if request_id is None and isinstance(response, Mapping):
        request_id = response.get("request_id")
    return str(request_id) if request_id else None
