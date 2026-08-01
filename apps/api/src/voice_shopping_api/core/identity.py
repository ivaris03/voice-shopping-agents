from typing import Annotated
from uuid import UUID

from fastapi import Header

DEFAULT_CUSTOMER_ID = UUID("00000000-0000-4000-8000-000000000101")
DEFAULT_MERCHANT_OWNER_ID = UUID("00000000-0000-4000-8000-000000000002")


async def current_user_id(
    x_user_id: Annotated[UUID | None, Header(alias="X-User-ID")] = None,
) -> UUID:
    return x_user_id or DEFAULT_CUSTOMER_ID


async def current_merchant_owner_id(
    x_merchant_owner_id: Annotated[UUID | None, Header(alias="X-Merchant-Owner-ID")] = None,
) -> UUID:
    return x_merchant_owner_id or DEFAULT_MERCHANT_OWNER_ID
