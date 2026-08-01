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


class CategoryOut(ApiModel):
    id: UUID
    category_l1: str
    category_l2: str
    required_slots: list[str]
    optional_slots: list[str]
    created_at: datetime
    updated_at: datetime


class CategoryCreate(ApiModel):
    category_l1: str = Field(min_length=1, max_length=100)
    category_l2: str = Field(min_length=1, max_length=100)
    required_slots: list[SlotKey] = Field(default_factory=list)
    optional_slots: list[SlotKey] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_slots(self) -> "CategoryCreate":
        self.required_slots = list(dict.fromkeys(self.required_slots))
        self.optional_slots = list(dict.fromkeys(self.optional_slots))
        duplicated = set(self.required_slots) & set(self.optional_slots)
        if duplicated:
            raise ValueError(f"槽位不能同时为必填和选填：{'、'.join(sorted(duplicated))}")
        return self


class CategoryUpdate(ApiModel):
    category_l1: str | None = Field(default=None, min_length=1, max_length=100)
    category_l2: str | None = Field(default=None, min_length=1, max_length=100)
    required_slots: list[SlotKey] | None = None
    optional_slots: list[SlotKey] | None = None


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


class OrderCreate(ApiModel):
    product_id: UUID
    quantity: int = Field(default=1, gt=0, le=99)
    idempotency_key: str = Field(min_length=1, max_length=120)
    session_id: UUID | None = None
    source_turn_id: UUID | None = None


class BehaviorCreate(ApiModel):
    product_id: UUID
    event_type: Literal["click"] = "click"


class ItemsResponse[T](ApiModel):
    items: list[T]
