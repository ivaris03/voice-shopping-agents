from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from voice_shopping_api.core.database import get_db_session
from voice_shopping_api.core.identity import Principal, create_access_token, current_principal
from voice_shopping_api.schemas.auth import CurrentUserOut, LoginRequest, LoginResponse

router = APIRouter()
Db = Annotated[AsyncSession, Depends(get_db_session)]
CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="手机号、密码错误或账号已停用",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _user_out(principal: Principal) -> CurrentUserOut:
    return CurrentUserOut(
        id=principal.user_id,
        email=principal.email,
        display_name=principal.display_name,
        role=principal.role,
    )


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, session: Db) -> LoginResponse:
    result = await session.execute(
        text(
            """
            SELECT id, email, display_name, role
            FROM users
            WHERE phone = :phone
              AND status = 'active'
              AND password_hash = crypt(:password, password_hash)
            LIMIT 2
            """
        ),
        {"phone": payload.phone, "password": payload.password},
    )
    matched_users = result.mappings().all()
    if len(matched_users) != 1:
        raise _invalid_credentials()
    row = matched_users[0]
    principal = Principal(
        user_id=row["id"],
        email=str(row["email"]),
        display_name=str(row["display_name"]),
        role=row["role"],
    )
    token, expires_in = create_access_token(principal)
    return LoginResponse(access_token=token, expires_in=expires_in, user=_user_out(principal))


@router.get("/me", response_model=CurrentUserOut)
async def me(principal: CurrentPrincipal) -> CurrentUserOut:
    return _user_out(principal)
