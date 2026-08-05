import re
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from voice_shopping_api.schemas.domain import ApiModel

UserRole = Literal["customer", "merchant", "platform"]


class LoginRequest(ApiModel):
    phone: str = Field(min_length=6, max_length=32)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        normalized = re.sub(r"[\s()\-]", "", value)
        if not re.fullmatch(r"\+?[0-9]{6,31}", normalized):
            raise ValueError("请输入有效的手机号")
        return normalized


class CurrentUserOut(ApiModel):
    id: UUID
    email: str
    display_name: str
    role: UserRole


class LoginResponse(ApiModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: CurrentUserOut
