from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from voice_shopping_api.modules.catalog.profile import (
    merge_static_profile_patches,
    update_static_profile,
)


async def finalize_session_profile(
    session: AsyncSession,
    session_id: UUID,
    user_id: UUID,
    profile_updates: Mapping[str, Any] | None = None,
    *,
    close_session: bool,
) -> dict[str, Any]:
    """Merge the latest conversation facts into the static profile.

    The latest persisted state is the durable source for facts collected by
    text/audio turns. Explicit facts supplied by a close event take precedence.
    Repeated finalization is safe because the profile merge is idempotent and
    closing an already closed session is a no-op.
    """
    session_result = await session.execute(
        text(
            """
            SELECT status
            FROM sessions
            WHERE id = :session_id AND user_id = :user_id
            FOR UPDATE
            """
        ),
        {"session_id": session_id, "user_id": user_id},
    )
    session_row = session_result.mappings().first()

    state_result = await session.execute(
        text(
            """
            SELECT workflow_state
            FROM session_states
            WHERE session_id = :session_id
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"session_id": session_id},
    )
    stored_state = state_result.scalar_one_or_none()
    stored_updates = (
        stored_state.get("user_profile_updates", {})
        if isinstance(stored_state, Mapping)
        else {}
    )
    merged_updates = merge_static_profile_patches(stored_updates, profile_updates)
    updated_fields = await update_static_profile(session, user_id, merged_updates)

    if close_session and session_row is not None:
        await session.execute(
            text(
                """
                UPDATE sessions
                SET status = 'closed',
                    ended_at = COALESCE(ended_at, CURRENT_TIMESTAMP),
                    last_active_at = CURRENT_TIMESTAMP
                WHERE id = :session_id AND user_id = :user_id AND status = 'active'
                """
            ),
            {"session_id": session_id, "user_id": user_id},
        )

    return {
        "sessionId": str(session_id),
        "status": "closed" if close_session and session_row is not None else "active",
        "updatedFields": updated_fields,
    }
