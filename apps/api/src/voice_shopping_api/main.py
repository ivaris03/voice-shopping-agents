import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from voice_shopping_api import __version__
from voice_shopping_api.agents.checkpointer import close_checkpointer
from voice_shopping_api.api.router import api_router
from voice_shopping_api.core.catalog_cache import catalog_cache
from voice_shopping_api.core.config import get_settings
from voice_shopping_api.core.database import engine
from voice_shopping_api.core.embeddings import close_product_embedding_cache
from voice_shopping_api.core.taxonomy import close_taxonomy_cache, start_taxonomy_cache
from voice_shopping_api.realtime.hub import hub as realtime_hub
from voice_shopping_api.realtime.router import router as realtime_router
from voice_shopping_api.schemas.common import HealthResponse

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if settings.langsmith_api_key:
        os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
        os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
        os.environ.setdefault("LANGSMITH_TRACING", "true")
    await start_taxonomy_cache()
    yield
    await close_taxonomy_cache()
    await close_checkpointer()
    await catalog_cache.close()
    await close_product_embedding_cache()
    await realtime_hub.close()
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix=settings.api_v1_prefix)
app.include_router(realtime_router)


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    return HealthResponse(status="ok", service=settings.app_name, version=__version__)
