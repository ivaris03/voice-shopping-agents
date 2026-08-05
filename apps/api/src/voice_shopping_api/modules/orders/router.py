from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from voice_shopping_api.core.catalog_cache import CatalogCache, get_catalog_cache
from voice_shopping_api.core.database import get_db_session
from voice_shopping_api.core.identity import current_user_id
from voice_shopping_api.core.queries import ORDER_COLUMNS, commit_or_conflict, rows
from voice_shopping_api.modules.orders.service import (
    cancel_order,
    confirm_order,
    create_pending_order,
)
from voice_shopping_api.modules.sessions.service import finalize_session_profile
from voice_shopping_api.schemas.domain import (
    CatalogOrderCreate,
    ItemsResponse,
    OrderCreate,
    OrderOut,
)

router = APIRouter()
Db = Annotated[AsyncSession, Depends(get_db_session)]
UserId = Annotated[UUID, Depends(current_user_id)]
Cache = Annotated[CatalogCache, Depends(get_catalog_cache)]


@router.get("/mine", response_model=ItemsResponse[OrderOut])
async def list_my_orders(session: Db, user_id: UserId) -> dict[str, object]:
    result = await session.execute(
        text(
            f"SELECT {ORDER_COLUMNS} FROM orders o WHERE o.user_id = :id ORDER BY o.created_at DESC"
        ),
        {"id": user_id},
    )
    return {"items": rows(result)}


@router.post("", response_model=OrderOut, status_code=201)
async def create_order(
    payload: CatalogOrderCreate, session: Db, user_id: UserId
) -> dict[str, object]:
    # The catalog endpoint has no server-owned conversation context. Retaining
    # only its public fields also lets old clients with a local sessionId keep
    # checking out without violating the orders/session foreign key.
    order = await create_pending_order(
        session,
        user_id,
        OrderCreate(**payload.model_dump()),
    )
    await commit_or_conflict(session, "幂等键或订单数据冲突")
    return order


@router.post("/{order_id}/confirm", response_model=OrderOut)
async def confirm(
    order_id: UUID, session: Db, user_id: UserId, cache: Cache
) -> dict[str, object]:
    order = await confirm_order(session, user_id, order_id)
    if order["status"] in {"success", "fail"} and order.get("session_id"):
        await finalize_session_profile(
            session,
            order["session_id"],
            user_id,
            close_session=True,
        )
    await session.commit()
    if order["status"] == "success":
        await cache.invalidate()
    return order


@router.post("/{order_id}/cancel", response_model=OrderOut)
async def cancel(order_id: UUID, session: Db, user_id: UserId) -> dict[str, object]:
    order = await cancel_order(session, user_id, order_id)
    if order["status"] == "fail" and order.get("session_id"):
        await finalize_session_profile(
            session,
            order["session_id"],
            user_id,
            close_session=True,
        )
    await session.commit()
    return order
