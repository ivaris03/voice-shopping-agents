import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from voice_shopping_api.core.catalog_cache import CatalogCache, get_catalog_cache
from voice_shopping_api.core.database import get_db_session
from voice_shopping_api.core.embeddings import embed_product_text
from voice_shopping_api.core.identity import current_merchant_owner_id
from voice_shopping_api.core.product_embedding import embedding_text_for_product
from voice_shopping_api.core.queries import (
    MERCHANT_COLUMNS,
    ORDER_COLUMNS,
    PRODUCT_COLUMNS,
    commit_or_conflict,
    owned_merchant_exists,
    row_or_404,
    rows,
)
from voice_shopping_api.core.taxonomy import list_categories, validate_product_taxonomy
from voice_shopping_api.schemas.domain import (
    CategoryOut,
    ItemsResponse,
    MerchantCreate,
    MerchantOut,
    MerchantUpdate,
    OrderOut,
    ProductCreate,
    ProductOut,
    ProductUpdate,
)

router = APIRouter()
Db = Annotated[AsyncSession, Depends(get_db_session)]
OwnerId = Annotated[UUID, Depends(current_merchant_owner_id)]
Cache = Annotated[CatalogCache, Depends(get_catalog_cache)]

# 仅这些字段参与向量拼装；改 sku/stock/status/image_urls 不需要重算向量。
EMBEDDING_FIELDS = frozenset(
    {
        "name",
        "category_l1",
        "category_l2",
        "brand",
        "description",
        "attributes",
        "selling_points",
        "price",
    }
)


@router.get("/categories", response_model=ItemsResponse[CategoryOut])
async def list_available_categories(session: Db) -> dict[str, object]:
    return {"items": await list_categories(session)}


@router.get("/stores", response_model=ItemsResponse[MerchantOut])
async def list_owned_stores(session: Db, owner_id: OwnerId, cache: Cache) -> dict[str, object]:
    async def load() -> dict[str, object]:
        result = await session.execute(
            text(
                f"""
                SELECT {MERCHANT_COLUMNS}
                FROM merchants m
                LEFT JOIN products p ON p.merchant_id = m.id AND p.deleted_at IS NULL
                WHERE m.owner_user_id = :owner_id AND m.deleted_at IS NULL
                GROUP BY m.id ORDER BY m.created_at
                """
            ),
            {"owner_id": owner_id},
        )
        return {"items": rows(result)}

    return await cache.get_or_load("owned-stores", {"owner_id": owner_id}, load)


@router.post("/stores", response_model=MerchantOut, status_code=201)
async def create_store(
    payload: MerchantCreate, session: Db, owner_id: OwnerId, cache: Cache
) -> dict[str, object]:
    result = await session.execute(
        text(
            """
            INSERT INTO merchants (owner_user_id, name, slug, description, logo_url, contact_phone)
            VALUES (:owner_id, :name, :slug, :description, :logo_url, :contact_phone)
            RETURNING id
            """
        ),
        {"owner_id": owner_id, **payload.model_dump()},
    )
    merchant_id = result.scalar_one()
    await commit_or_conflict(session, "店铺标识已存在")
    await cache.invalidate()
    created = await session.execute(
        text(
            f"""
            SELECT {MERCHANT_COLUMNS} FROM merchants m
            LEFT JOIN products p ON false WHERE m.id = :id GROUP BY m.id
            """
        ),
        {"id": merchant_id},
    )
    return row_or_404(created, "店铺不存在")


@router.patch("/stores/{store_id}", response_model=MerchantOut)
async def update_store(
    store_id: UUID, payload: MerchantUpdate, session: Db, owner_id: OwnerId, cache: Cache
) -> dict[str, object]:
    values = payload.model_dump(exclude_unset=True)
    if values:
        assignments = ", ".join(f"{key} = :{key}" for key in values)
        result = await session.execute(
            text(
                f"""
                UPDATE merchants SET {assignments}
                WHERE id = :id AND owner_user_id = :owner_id AND deleted_at IS NULL
                RETURNING id
                """
            ),
            {**values, "id": store_id, "owner_id": owner_id},
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="店铺不存在")
        await commit_or_conflict(session, "店铺标识已存在")
        await cache.invalidate()
    current = await session.execute(
        text(
            f"""
            SELECT {MERCHANT_COLUMNS} FROM merchants m
            LEFT JOIN products p ON p.merchant_id = m.id AND p.deleted_at IS NULL
            WHERE m.id = :id AND m.owner_user_id = :owner_id AND m.deleted_at IS NULL
            GROUP BY m.id
            """
        ),
        {"id": store_id, "owner_id": owner_id},
    )
    return row_or_404(current, "店铺不存在")


@router.delete("/stores/{store_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_store(store_id: UUID, session: Db, owner_id: OwnerId, cache: Cache) -> Response:
    result = await session.execute(
        text(
            """
            UPDATE merchants SET deleted_at = CURRENT_TIMESTAMP
            WHERE id = :id AND owner_user_id = :owner_id AND deleted_at IS NULL
            RETURNING id
            """
        ),
        {"id": store_id, "owner_id": owner_id},
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="店铺不存在")
    await session.execute(
        text(
            """
            UPDATE products SET deleted_at = CURRENT_TIMESTAMP
            WHERE merchant_id = :id AND deleted_at IS NULL
            """
        ),
        {"id": store_id},
    )
    await session.commit()
    await cache.invalidate()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/products", response_model=ItemsResponse[ProductOut])
async def list_owned_products(session: Db, owner_id: OwnerId, cache: Cache) -> dict[str, object]:
    async def load() -> dict[str, object]:
        result = await session.execute(
            text(
                f"""
                SELECT {PRODUCT_COLUMNS} FROM products p JOIN merchants m ON m.id = p.merchant_id
                WHERE m.owner_user_id = :owner_id AND p.deleted_at IS NULL AND m.deleted_at IS NULL
                ORDER BY p.created_at DESC
                """
            ),
            {"owner_id": owner_id},
        )
        return {"items": rows(result)}

    return await cache.get_or_load("owned-products", {"owner_id": owner_id}, load)


def _product_params(payload: ProductCreate | ProductUpdate) -> dict[str, Any]:
    values = payload.model_dump(exclude_unset=isinstance(payload, ProductUpdate))
    for key in ("attributes",):
        if key in values:
            values[key] = json.dumps(values[key], ensure_ascii=False)
    return values


@router.post("/products", response_model=ProductOut, status_code=201)
async def create_product(
    payload: ProductCreate, session: Db, owner_id: OwnerId, cache: Cache
) -> dict[str, object]:
    if not await owned_merchant_exists(session, payload.merchant_id, owner_id):
        raise HTTPException(status_code=404, detail="店铺不存在")
    try:
        payload.attributes = await validate_product_taxonomy(
            session, payload.category_l1, payload.category_l2, payload.attributes
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    values = _product_params(payload)
    text_product = {**values, "attributes": payload.attributes}
    values["embedding"] = await embed_product_text(embedding_text_for_product(text_product))
    result = await session.execute(
        text(
            """
            INSERT INTO products (
                merchant_id, sku, name, category_l1, category_l2, brand, description,
                price, stock, attributes, selling_points, image_urls, status, embedding
            ) VALUES (
                :merchant_id, :sku, :name, :category_l1, :category_l2, :brand, :description,
                :price, :stock, CAST(:attributes AS jsonb), :selling_points, :image_urls, :status,
                CAST(:embedding AS vector)
            ) RETURNING id
            """
        ),
        values,
    )
    product_id = result.scalar_one()
    await commit_or_conflict(session, "同一店铺内 SKU 已存在")
    await cache.invalidate()
    created = await session.execute(
        text(
            f"""
            SELECT {PRODUCT_COLUMNS} FROM products p
            JOIN merchants m ON m.id = p.merchant_id WHERE p.id = :id
            """
        ),
        {"id": product_id},
    )
    return row_or_404(created, "商品不存在")


@router.patch("/products/{product_id}", response_model=ProductOut)
async def update_product(
    product_id: UUID, payload: ProductUpdate, session: Db, owner_id: OwnerId, cache: Cache
) -> dict[str, object]:
    values = _product_params(payload)
    if values:
        needs_embedding_review = bool(EMBEDDING_FIELDS & values.keys())
        existing = await session.execute(
            text(
                """
                SELECT p.name, p.category_l1, p.category_l2, p.brand, p.description,
                       p.price, p.attributes, p.selling_points
                FROM products p JOIN merchants m ON m.id = p.merchant_id
                WHERE p.id = :id AND m.owner_user_id = :owner_id
                  AND p.deleted_at IS NULL AND m.deleted_at IS NULL
                """
            ),
            {"id": product_id, "owner_id": owner_id},
        )
        current_product = existing.mappings().first()
        if current_product is None:
            raise HTTPException(status_code=404, detail="商品不存在")
        if needs_embedding_review:
            category_l1 = str(values.get("category_l1", current_product["category_l1"]))
            category_l2 = values.get("category_l2", current_product["category_l2"])
            category_changed = (
                category_l1 != current_product["category_l1"]
                or category_l2 != current_product["category_l2"]
            )
            if category_changed and "attributes" not in values:
                source_attributes: dict[str, Any] = {}
            elif "attributes" in values:
                source_attributes = json.loads(values["attributes"])
            else:
                source_attributes = dict(current_product["attributes"])
            try:
                values["attributes"] = json.dumps(
                    await validate_product_taxonomy(
                        session, category_l1, str(category_l2), source_attributes
                    ),
                    ensure_ascii=False,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            text_product = {
                **dict(current_product),
                **{key: value for key, value in values.items() if key in EMBEDDING_FIELDS},
            }
            if isinstance(text_product["attributes"], str):
                text_product["attributes"] = json.loads(text_product["attributes"])
            # 拼装文本没变（如价格仍在同一价格带内）时保留原向量，避免无谓调用。
            if embedding_text_for_product(current_product) != embedding_text_for_product(
                text_product
            ):
                values["embedding"] = await embed_product_text(
                    embedding_text_for_product(text_product)
                )
        expressions = []
        for key in values:
            if key == "attributes":
                expressions.append(f"{key} = CAST(:{key} AS jsonb)")
            elif key == "embedding":
                expressions.append(f"{key} = CAST(:{key} AS vector)")
            else:
                expressions.append(f"{key} = :{key}")
        result = await session.execute(
            text(
                f"""
                UPDATE products p SET {", ".join(expressions)}
                FROM merchants m
                WHERE p.id = :id AND p.merchant_id = m.id AND m.owner_user_id = :owner_id
                  AND p.deleted_at IS NULL AND m.deleted_at IS NULL
                RETURNING p.id
                """
            ),
            {**values, "id": product_id, "owner_id": owner_id},
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="商品不存在")
        await commit_or_conflict(session, "同一店铺内 SKU 已存在")
        await cache.invalidate()
    current = await session.execute(
        text(
            f"""
            SELECT {PRODUCT_COLUMNS} FROM products p JOIN merchants m ON m.id = p.merchant_id
            WHERE p.id = :id AND m.owner_user_id = :owner_id AND p.deleted_at IS NULL
            """
        ),
        {"id": product_id, "owner_id": owner_id},
    )
    return row_or_404(current, "商品不存在")


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: UUID, session: Db, owner_id: OwnerId, cache: Cache
) -> Response:
    result = await session.execute(
        text(
            """
            UPDATE products p SET deleted_at = CURRENT_TIMESTAMP
            FROM merchants m
            WHERE p.id = :id AND p.merchant_id = m.id AND m.owner_user_id = :owner_id
              AND p.deleted_at IS NULL
            RETURNING p.id
            """
        ),
        {"id": product_id, "owner_id": owner_id},
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="商品不存在")
    await session.commit()
    await cache.invalidate()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/orders", response_model=ItemsResponse[OrderOut])
async def list_store_orders(session: Db, owner_id: OwnerId) -> dict[str, object]:
    result = await session.execute(
        text(
            f"""
            SELECT {ORDER_COLUMNS} FROM orders o JOIN merchants m ON m.id = o.merchant_id
            WHERE m.owner_user_id = :owner_id ORDER BY o.created_at DESC
            """
        ),
        {"owner_id": owner_id},
    )
    return {"items": rows(result)}
