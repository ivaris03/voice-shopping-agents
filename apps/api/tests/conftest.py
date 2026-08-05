from __future__ import annotations

import os
from collections.abc import AsyncIterator

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from voice_shopping_api.core.catalog_cache import CatalogCache, get_catalog_cache
from voice_shopping_api.core.config import get_settings
from voice_shopping_api.core.database import get_db_session
from voice_shopping_api.core.migrations import apply_migrations, asyncpg_url, seed_demo_data
from voice_shopping_api.main import app

TEST_DATABASE_ENV = "VOICE_SHOPPING_TEST_DATABASE_URL"
_MISSING_OVERRIDE = object()


def _database_name(database_url: str) -> str | None:
    return make_url(database_url).database


@pytest.fixture(scope="session")
def e2e_database_url() -> str:
    database_url = os.getenv(TEST_DATABASE_ENV, "").strip()
    if not database_url:
        pytest.skip(f"E2E requires an explicit {TEST_DATABASE_ENV}")
    if _database_name(database_url) == _database_name(get_settings().database_url):
        pytest.fail(
            f"{TEST_DATABASE_ENV} must use a database distinct from the application database"
        )
    return database_url


async def _rebuild_e2e_database(database_url: str) -> None:
    """Reset only the explicitly supplied disposable E2E database."""

    connection = await asyncpg.connect(asyncpg_url(database_url))
    try:
        await connection.execute("DROP SCHEMA IF EXISTS public CASCADE")
        await connection.execute("CREATE SCHEMA public")
    finally:
        await connection.close()
    await apply_migrations(database_url)
    await seed_demo_data(database_url)


@pytest_asyncio.fixture(scope="module")
async def e2e_engine(e2e_database_url: str) -> AsyncIterator[AsyncEngine]:
    try:
        await _rebuild_e2e_database(e2e_database_url)
    except Exception as exc:  # noqa: BLE001 - a configured test dependency must be actionable
        pytest.fail(f"Unable to rebuild the configured E2E database: {exc}")

    engine = create_async_engine(e2e_database_url, pool_pre_ping=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def e2e_connection(e2e_engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    connection = await e2e_engine.connect()
    transaction = await connection.begin()
    try:
        yield connection
    finally:
        await transaction.rollback()
        await connection.close()


@pytest_asyncio.fixture
async def e2e_session(e2e_connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    session_factory = async_sessionmaker(
        bind=e2e_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def e2e_committing_session(e2e_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Use real commits when an E2E scenario spans several persisted turns."""

    session_factory = async_sessionmaker(bind=e2e_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def e2e_client(e2e_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    previous_override = app.dependency_overrides.get(get_db_session, _MISSING_OVERRIDE)
    previous_cache_override = app.dependency_overrides.get(get_catalog_cache, _MISSING_OVERRIDE)

    async def override_db_session() -> AsyncIterator[AsyncSession]:
        yield e2e_session

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_catalog_cache] = lambda: CatalogCache(enabled=False)
    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        if previous_override is _MISSING_OVERRIDE:
            app.dependency_overrides.pop(get_db_session, None)
        else:
            app.dependency_overrides[get_db_session] = previous_override
        if previous_cache_override is _MISSING_OVERRIDE:
            app.dependency_overrides.pop(get_catalog_cache, None)
        else:
            app.dependency_overrides[get_catalog_cache] = previous_cache_override
