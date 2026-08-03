from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


def to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class MerchantOut(ApiModel):
    id: UUID
    owner_user_id: UUID
    owner_display_name: str | None = None
    name: str
    slug: str
    description: str | None = None
    logo_url: str | None = None
    contact_phone: str | None = None
    is_enabled: bool
    disabled_reason: str | None = None
    product_count: int = 0
    created_at: datetime
    updated_at: datetime


class MerchantCreate(ApiModel):
    name: str = Field(min_length=1, max_length=150)
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)
    description: str | None = None
    logo_url: str | None = None
    contact_phone: str | None = None


class MerchantUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)
    description: str | None = None
    logo_url: str | None = None
    contact_phone: str | None = None


class MerchantStatusUpdate(ApiModel):
    is_enabled: bool
    disabled_reason: str | None = None

    @model_validator(mode="after")
    def validate_reason(self) -> "MerchantStatusUpdate":
        if not self.is_enabled and not (self.disabled_reason or "").strip():
            raise ValueError("禁用商家时必须填写原因")
        return self


SlotKey = Annotated[str, Field(pattern=r"^[a-z][A-Za-z0-9]*$", max_length=100)]
SlotEnumValue = str | int | float | bool


class CategoryL1Out(ApiModel):
    id: UUID
    code: str
    created_at: datetime
    updated_at: datetime


class CategoryL1Create(ApiModel):
    code: str = Field(min_length=1, max_length=100)


class CategorySlotOut(ApiModel):
    id: UUID
    key: str
    is_required: bool
    enum_values: list[SlotEnumValue]


class CategorySlotCreate(ApiModel):
    key: SlotKey
    is_required: bool
    enum_values: list[SlotEnumValue] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_enum_values(self) -> "CategorySlotCreate":
        normalized: list[SlotEnumValue] = []
        for value in self.enum_values:
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    raise ValueError("枚举值不能为空")
            if value not in normalized:
                normalized.append(value)
        if not normalized:
            raise ValueError("创建槽位时必须同时提供枚举值")
        self.enum_values = normalized
        return self


class CategorySlotUpdate(ApiModel):
    is_required: bool | None = None
    enum_values: list[SlotEnumValue] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_enum_values(self) -> "CategorySlotUpdate":
        if self.enum_values is None:
            return self
        normalized: list[SlotEnumValue] = []
        for value in self.enum_values:
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    raise ValueError("枚举值不能为空")
            if value not in normalized:
                normalized.append(value)
        if not normalized:
            raise ValueError("槽位必须至少保留一个枚举值")
        self.enum_values = normalized
        return self


class CategoryOut(ApiModel):
    id: UUID
    category_l1_id: UUID
    category_l1: str
    category_l2: str
    required_slots: list[str]
    optional_slots: list[str]
    slots: list[CategorySlotOut]
    created_at: datetime
    updated_at: datetime


class CategoryCreate(ApiModel):
    category_l1_id: UUID
    category_l2: str = Field(min_length=1, max_length=100)


class CategoryUpdate(ApiModel):
    category_l1_id: UUID | None = None
    category_l2: str | None = Field(default=None, min_length=1, max_length=100)


class ProductOut(ApiModel):
    id: UUID
    merchant_id: UUID
    merchant_name: str | None = None
    sku: str
    name: str
    category_l1: str
    category_l2: str
    brand: str | None = None
    description: str
    price: Decimal
    stock: int
    attributes: dict[str, Any]
    selling_points: list[str]
    image_urls: list[str]
    status: Literal["draft", "on_sale", "off_sale"]
    created_at: datetime
    updated_at: datetime


class ProductCreate(ApiModel):
    merchant_id: UUID
    sku: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    category_l1: str = Field(min_length=1, max_length=100)
    category_l2: str = Field(min_length=1, max_length=100)
    brand: str | None = Field(default=None, max_length=100)
    description: str = ""
    price: Decimal = Field(ge=0)
    stock: int = Field(ge=0)
    attributes: dict[str, Any] = Field(default_factory=dict)
    selling_points: list[str] = Field(default_factory=list)
    image_urls: list[str] = Field(default_factory=list)
    status: Literal["draft", "on_sale", "off_sale"] = "draft"


class ProductUpdate(ApiModel):
    sku: str | None = Field(default=None, min_length=1, max_length=80)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    category_l1: str | None = Field(default=None, min_length=1, max_length=100)
    category_l2: str | None = Field(default=None, min_length=1, max_length=100)
    brand: str | None = Field(default=None, max_length=100)
    description: str | None = None
    price: Decimal | None = Field(default=None, ge=0)
    stock: int | None = Field(default=None, ge=0)
    attributes: dict[str, Any] | None = None
    selling_points: list[str] | None = None
    image_urls: list[str] | None = None
    status: Literal["draft", "on_sale", "off_sale"] | None = None


class OrderOut(ApiModel):
    id: UUID
    user_id: UUID
    merchant_id: UUID
    product_id: UUID
    status: Literal["pending", "success", "fail"]
    quantity: int
    unit_price: Decimal
    total_amount: Decimal
    merchant_snapshot: dict[str, Any]
    product_snapshot: dict[str, Any]
    failure_reason: str | None = None
    expires_at: datetime
    confirmed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CatalogOrderCreate(ApiModel):
    """Public payload for a direct catalog checkout."""

    product_id: UUID
    quantity: int = Field(default=1, gt=0, le=99)
    idempotency_key: str = Field(min_length=1, max_length=120)


class OrderCreate(CatalogOrderCreate):
    """Internal order command used by the conversational workflow."""

    session_id: UUID | None = None
    source_turn_id: UUID | None = None


class BehaviorCreate(ApiModel):
    product_id: UUID
    event_type: Literal["click"] = "click"


class UserProfileStaticPatch(ApiModel):
    """Facts supplied by a trusted channel when a conversation is finalized."""

    gender: str | None = Field(default=None, max_length=8)
    age: int | None = Field(default=None, ge=0, le=120)
    city: str | None = Field(default=None, max_length=32)
    height_cm: int | None = Field(default=None, ge=50, le=250)
    weight_kg: int | None = Field(default=None, ge=10, le=300)
    skin_type: str | None = Field(default=None, max_length=16)
    tech_savvy: str | None = Field(default=None, max_length=16)
    budget_band: str | None = Field(default=None, max_length=16)
    budget: Decimal | None = Field(default=None, ge=0)
    locale: str | None = Field(default=None, max_length=16)


class SessionClose(ApiModel):
    profile: UserProfileStaticPatch | None = None
    reason: Literal["order_completed", "page_closed", "user_ended", "disconnect"] = "user_ended"


class ItemsResponse[T](ApiModel):
    items: list[T]
