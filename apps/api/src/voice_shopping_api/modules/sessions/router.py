from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from voice_shopping_api.core.database import get_db_session
from voice_shopping_api.core.identity import current_user_id
from voice_shopping_api.core.queries import commit_or_conflict
from voice_shopping_api.core.session import stable_uuid
from voice_shopping_api.modules.sessions.service import finalize_session_profile
from voice_shopping_api.schemas.domain import SessionClose

router = APIRouter()
Db = Annotated[AsyncSession, Depends(get_db_session)]
UserId = Annotated[UUID, Depends(current_user_id)]


@router.post("/{session_id}/close")
async def close_session(
    session_id: str,
    session: Db,
    user_id: UserId,
    payload: SessionClose | None = None,
) -> dict[str, object]:
    result = await finalize_session_profile(
        session,
        stable_uuid(session_id),
        user_id,
        payload.profile.model_dump(exclude_none=True) if payload and payload.profile else None,
        close_session=True,
    )
    await commit_or_conflict(session, "会话关闭失败")
    return result
