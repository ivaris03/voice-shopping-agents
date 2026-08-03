import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from voice_shopping_api.core.config import get_settings
from voice_shopping_api.core.database import get_db_session
from voice_shopping_api.main import app


@pytest_asyncio.fixture(scope="module")
async def e2e_engine() -> AsyncIterator[AsyncEngine]:
    database_url = os.getenv("VOICE_SHOPPING_TEST_DATABASE_URL") or get_settings().database_url
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - local integration dependency may be absent
        await engine.dispose()
        pytest.skip(f"E2E database is unavailable: {exc}")

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
async def e2e_client(e2e_connection: AsyncConnection) -> AsyncIterator[AsyncClient]:
    session_factory = async_sessionmaker(
        bind=e2e_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    async def override_db_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()
