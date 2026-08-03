"""Create and seed the explicitly configured disposable E2E database."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import asyncpg
from sqlalchemy.engine import URL, make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from voice_shopping_api.core.config import get_settings  # noqa: E402
from voice_shopping_api.core.migrations import (  # noqa: E402
    apply_migrations,
    asyncpg_url,
    seed_demo_data,
)


def _database_identity(database_url: str) -> tuple[str | None, int | None, str | None]:
    url = make_url(database_url)
    return url.host, url.port or 5432, url.database


def _quoted_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _render_database_url(url: URL) -> str:
    """Render a SQLAlchemy URL for a driver, retaining its credential internally."""

    return url.render_as_string(hide_password=False)


async def _ensure_database(database_url: str) -> None:
    target = make_url(database_url)
    if not target.database:
        raise ValueError("Test database URL must include a database name")
    maintenance_url = target.set(database="postgres")
    connection = await asyncpg.connect(asyncpg_url(_render_database_url(maintenance_url)))
    try:
        exists = await connection.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", target.database
        )
        if not exists:
            await connection.execute(f"CREATE DATABASE {_quoted_identifier(target.database)}")
    finally:
        await connection.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare the isolated Voice Shopping E2E database")
    parser.add_argument(
        "--database-url",
        default=os.getenv("VOICE_SHOPPING_TEST_DATABASE_URL", ""),
    )
    args = parser.parse_args()
    database_url = str(args.database_url).strip()
    if not database_url:
        raise SystemExit("Set VOICE_SHOPPING_TEST_DATABASE_URL before preparing the E2E database")
    if _database_identity(database_url) == _database_identity(get_settings().database_url):
        raise SystemExit(
            "VOICE_SHOPPING_TEST_DATABASE_URL must not point to the application database"
        )

    await _ensure_database(database_url)
    applied = await apply_migrations(database_url)
    await seed_demo_data(database_url)
    print(f"Test database ready ({len(applied)} migration(s) applied)")


if __name__ == "__main__":
    asyncio.run(main())
