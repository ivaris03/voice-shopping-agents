from collections.abc import Mapping
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from voice_shopping_api.modules.catalog.profile import (
    merge_static_profile_patches,
    update_static_profile,
)

SESSION_NOT_FOUND_DETAIL = "会话不存在或无权访问"
SESSION_CLOSED_DETAIL = "会话已关闭，无法继续操作"


async def _load_session_row(
    session: AsyncSession,
    session_id: UUID,
    *,
    for_update: bool = False,
) -> dict[str, Any] | None:
    """Load the authoritative session row before touching session-scoped data."""
    lock_clause = " FOR UPDATE" if for_update else ""
    result = await session.execute(
        text(
            f"""
            SELECT id, user_id, status
            FROM sessions
            WHERE id = :session_id
            {lock_clause}
            """
        ),
        {"session_id": session_id},
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


def _validate_session_row(
    row: Mapping[str, Any] | None,
    user_id: UUID,
    *,
    require_active: bool,
) -> dict[str, Any]:
    if row is None or str(row.get("user_id")) != str(user_id):
        # Do not disclose whether a session key belongs to another user.
        raise HTTPException(status_code=404, detail=SESSION_NOT_FOUND_DETAIL)
    if require_active and row.get("status") != "active":
        raise HTTPException(status_code=409, detail=SESSION_CLOSED_DETAIL)
    return dict(row)


async def get_session_for_user(
    session: AsyncSession,
    session_id: UUID,
    user_id: UUID,
    *,
    require_active: bool = False,
    for_update: bool = False,
    missing_ok: bool = False,
) -> dict[str, Any] | None:
    """Return a session only when it belongs to ``user_id``.

    ``missing_ok`` is used by best-effort disconnect cleanup. Business paths
    should leave it false so an unknown or foreign session cannot be treated as
    a new conversation accidentally.
    """
    row = await _load_session_row(session, session_id, for_update=for_update)
    if row is None and missing_ok:
        return None
    return _validate_session_row(row, user_id, require_active=require_active)


async def ensure_active_session(
    session: AsyncSession,
    session_id: UUID,
    user_id: UUID,
) -> dict[str, Any]:
    """Create a new session atomically or lock the existing owned session."""
    insert_result = await session.execute(
        text(
            """
            INSERT INTO sessions (id, user_id)
            VALUES (:session_id, :user_id)
            ON CONFLICT (id) DO NOTHING
            RETURNING id
            """
        ),
        {"session_id": session_id, "user_id": user_id},
    )
    created = insert_result.scalar_one_or_none() is not None
    row = await _load_session_row(session, session_id, for_update=True)
    validated = _validate_session_row(row, user_id, require_active=True)
    # A checkpoint can outlive a rolled-back session insert. Do not restore a
    # stale thread on the first turn that creates a session.
    validated["_created"] = created
    return validated


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
    session_row = await get_session_for_user(
        session,
        session_id,
        user_id,
        for_update=True,
        missing_ok=not close_session,
    )
    if session_row is None:
        return {
            "sessionId": str(session_id),
            "status": "missing",
            "updatedFields": [],
        }

    # A closed session is terminal. Repeated close/disconnect callbacks remain
    # idempotent and must never reopen it. An explicit profile supplied with a
    # repeated close is still a valid close-time update; disconnect cleanup is
    # intentionally a no-op once the session is terminal.
    if session_row["status"] == "closed":
        updated_fields = (
            await update_static_profile(session, user_id, profile_updates)
            if close_session and profile_updates
            else []
        )
        return {
            "sessionId": str(session_id),
            "status": "closed",
            "updatedFields": updated_fields,
        }

    state_result = await session.execute(
        text(
            """
            SELECT ss.business_state
            FROM session_states AS ss
            JOIN sessions AS s ON s.id = ss.session_id AND s.user_id = :user_id
            WHERE ss.session_id = :session_id
            ORDER BY ss.created_at DESC
            LIMIT 1
            """
        ),
        {"session_id": session_id, "user_id": user_id},
    )
    stored_state = state_result.scalar_one_or_none()
    stored_updates = (
        stored_state.get("user_profile_updates", {})
        if isinstance(stored_state, Mapping)
        else {}
    )
    merged_updates = merge_static_profile_patches(stored_updates, profile_updates)
    updated_fields = await update_static_profile(session, user_id, merged_updates)

    if close_session:
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
        "status": "closed" if close_session else "active",
        "updatedFields": updated_fields,
    }
