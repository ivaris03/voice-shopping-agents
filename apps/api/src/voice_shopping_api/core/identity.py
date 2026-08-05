from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from voice_shopping_api.core.config import Settings, get_settings

UserRole = Literal["customer", "merchant", "platform"]
_VALID_ROLES = frozenset({"customer", "merchant", "platform"})
_bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: UUID
    email: str
    display_name: str
    role: UserRole


def _unauthorized(detail: str = "登录凭证无效或已过期") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _forbidden() -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="当前账号无权执行此操作")


def create_access_token(principal: Principal, settings: Settings | None = None) -> tuple[str, int]:
    settings = settings or get_settings()
    issued_at = datetime.now(UTC)
    expires_at = issued_at + timedelta(minutes=settings.jwt_access_token_ttl_minutes)
    payload = {
        "sub": str(principal.user_id),
        "email": principal.email,
        "name": principal.display_name,
        "role": principal.role,
        "typ": "access",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": issued_at,
        "exp": expires_at,
    }
    token = jwt.encode(payload, settings.jwt_signing_key, algorithm="HS256")
    return token, settings.jwt_access_token_ttl_minutes * 60


def principal_from_access_token(token: str, settings: Settings | None = None) -> Principal:
    settings = settings or get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_signing_key,
            algorithms=["HS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "role", "typ", "iat", "exp"]},
        )
        if payload.get("typ") != "access":
            raise jwt.InvalidTokenError("unexpected token type")
        role = payload.get("role")
        if role not in _VALID_ROLES:
            raise jwt.InvalidTokenError("unexpected role")
        return Principal(
            user_id=UUID(str(payload["sub"])),
            email=str(payload.get("email") or ""),
            display_name=str(payload.get("name") or ""),
            role=role,
        )
    except (jwt.InvalidTokenError, ValueError, TypeError) as exc:
        raise _unauthorized() from exc


async def current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("缺少 Bearer 登录凭证")
    return principal_from_access_token(credentials.credentials)


def _require_role(principal: Principal, role: UserRole) -> Principal:
    if principal.role != role:
        raise _forbidden()
    return principal


async def current_customer_principal(
    principal: Annotated[Principal, Depends(current_principal)],
) -> Principal:
    return _require_role(principal, "customer")


async def current_merchant_principal(
    principal: Annotated[Principal, Depends(current_principal)],
) -> Principal:
    return _require_role(principal, "merchant")


async def current_platform_principal(
    principal: Annotated[Principal, Depends(current_principal)],
) -> Principal:
    return _require_role(principal, "platform")


async def current_user_id(
    principal: Annotated[Principal, Depends(current_customer_principal)],
) -> UUID:
    return principal.user_id


async def current_merchant_owner_id(
    principal: Annotated[Principal, Depends(current_merchant_principal)],
) -> UUID:
    return principal.user_id


def websocket_customer_principal(token: str | None) -> Principal:
    if not token:
        raise _unauthorized("缺少 WebSocket 登录凭证")
    return _require_role(principal_from_access_token(token), "customer")
