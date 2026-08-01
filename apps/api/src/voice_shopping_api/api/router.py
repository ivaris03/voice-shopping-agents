from fastapi import APIRouter

from voice_shopping_api.modules.catalog.router import router as catalog_router
from voice_shopping_api.modules.merchant.router import router as merchant_router
from voice_shopping_api.modules.orders.router import router as orders_router
from voice_shopping_api.modules.platform.router import router as platform_router

api_router = APIRouter()
api_router.include_router(catalog_router, prefix="/catalog", tags=["catalog"])
api_router.include_router(orders_router, prefix="/orders", tags=["orders"])
api_router.include_router(merchant_router, prefix="/merchant", tags=["merchant"])
api_router.include_router(platform_router, prefix="/platform", tags=["platform"])
