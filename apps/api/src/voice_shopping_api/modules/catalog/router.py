from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from voice_shopping_api.core.catalog_cache import CatalogCache, get_catalog_cache
from voice_shopping_api.core.database import get_db_session
from voice_shopping_api.core.identity import current_user_id
from voice_shopping_api.core.queries import MERCHANT_COLUMNS, PRODUCT_COLUMNS, rows
from voice_shopping_api.core.taxonomy import list_categories
from voice_shopping_api.modules.catalog.profile import update_profiles
from voice_shopping_api.schemas.domain import (
    BehaviorCreate,
    CategoryOut,
    ItemsResponse,
    MerchantOut,
    ProductOut,
)

router = APIRouter()
Db = Annotated[AsyncSession, Depends(get_db_session)]
UserId = Annotated[UUID, Depends(current_user_id)]
Cache = Annotated[CatalogCache, Depends(get_catalog_cache)]


@router.get("/categories", response_model=ItemsResponse[CategoryOut])
async def list_supported_categories(session: Db) -> dict[str, object]:
    """Expose the platform-maintained category taxonomy to shopping customers."""
    return {"items": await list_categories(session)}


@router.get("/merchants", response_model=ItemsResponse[MerchantOut])
async def list_visible_merchants(session: Db, cache: Cache) -> dict[str, object]:
    async def load() -> dict[str, object]:
        result = await session.execute(
            text(
                f"""
                SELECT {MERCHANT_COLUMNS}
                FROM merchants m
                LEFT JOIN products p ON p.merchant_id = m.id
                    AND p.deleted_at IS NULL AND p.status = 'on_sale'
                WHERE m.deleted_at IS NULL AND m.is_enabled
                GROUP BY m.id
                ORDER BY m.created_at
                """
            )
        )
        return {"items": rows(result)}

    return await cache.get_or_load("visible-merchants", {}, load)


@router.get("/products", response_model=ItemsResponse[ProductOut])
async def list_visible_products(
    session: Db,
    cache: Cache,
    merchant_id: Annotated[UUID | None, Query(alias="merchantId")] = None,
    category: str | None = None,
    query: str | None = None,
) -> dict[str, object]:
    async def load() -> dict[str, object]:
        result = await session.execute(
            text(
                f"""
                SELECT {PRODUCT_COLUMNS}
                FROM products p JOIN merchants m ON m.id = p.merchant_id
                WHERE p.deleted_at IS NULL AND p.status = 'on_sale'
                  AND p.stock > 0 AND m.deleted_at IS NULL AND m.is_enabled
                  AND (CAST(:merchant_id AS uuid) IS NULL OR p.merchant_id = :merchant_id)
                  AND (CAST(:category AS text) IS NULL OR p.category_l2 = CAST(:category AS text))
                  AND (CAST(:query AS text) IS NULL
                       OR p.name ILIKE '%%' || CAST(:query AS text) || '%%'
                       OR p.brand ILIKE '%%' || CAST(:query AS text) || '%%')
                ORDER BY p.created_at, p.name
                LIMIT 100
                """
            ),
            {"merchant_id": merchant_id, "category": category, "query": query},
        )
        return {"items": rows(result)}

    return await cache.get_or_load(
        "visible-products",
        {"merchant_id": merchant_id, "category": category, "query": query},
        load,
    )


@router.post("/behaviors", status_code=202)
async def report_behavior(payload: BehaviorCreate, session: Db, user_id: UserId) -> dict[str, str]:
    await update_profiles(session, user_id, payload.product_id, payload.event_type)
    await session.commit()
    return {"status": "accepted"}
