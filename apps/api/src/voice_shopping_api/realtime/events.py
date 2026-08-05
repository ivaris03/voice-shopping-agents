"""Shared envelope helpers for realtime JSON control events."""

from collections.abc import Mapping
from typing import Any

SESSION_TURN_ID = "session"
SESSION_EVENT_SEQ = 0


def event_envelope(
    event_type: str,
    session_id: str,
    turn_id: str,
    seq: int,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the single wire shape used by every server JSON event."""
    return {
        "type": event_type,
        "sessionId": session_id,
        "turnId": turn_id,
        "seq": seq,
        "payload": dict(payload or {}),
    }
