"""Versioned SQL migrations for the application's PostgreSQL schema."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import asyncpg

PROJECT_ROOT = Path(__file__).resolve().parents[5]
MIGRATIONS_DIR = PROJECT_ROOT / "sql" / "migrations"
DEMO_DATA_PATH = PROJECT_ROOT / "sql" / "data.sql"
MIGRATION_TABLE = "voice_shopping_schema_migrations"
MIGRATION_LOCK_KEY = "voice-shopping-schema-migrations"
_MIGRATION_FILENAME = re.compile(r"^(?P<version>\d{8}_[a-z0-9][a-z0-9_-]*)\.sql$")
_TRANSACTION_CONTROL = re.compile(r"(?im)^\s*(?:begin|commit)\s*;\s*$")


@dataclass(frozen=True)
class Migration:
    """An immutable SQL migration discovered from ``sql/migrations``."""

    version: str
    path: Path
    sql: str
    checksum: str


def asyncpg_url(database_url: str) -> str:
    """Convert the application's SQLAlchemy async URL into an asyncpg DSN."""

    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def load_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Load ordered migration files and reject names that could sort ambiguously."""

    migrations: list[Migration] = []
    versions: set[str] = set()
    for path in sorted(directory.glob("*.sql")):
        match = _MIGRATION_FILENAME.fullmatch(path.name)
        if match is None:
            raise ValueError(f"Migration filename must start with YYYYMMDD_: {path.name}")
        version = match.group("version")
        if version in versions:
            raise ValueError(f"Duplicate migration version: {version}")
        sql = path.read_text(encoding="utf-8")
        if _TRANSACTION_CONTROL.search(sql):
            raise ValueError(f"Migration must not manage its own transaction: {path.name}")
        migrations.append(
            Migration(
                version=version,
                path=path,
                sql=sql,
                checksum=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            )
        )
        versions.add(version)
    return migrations


async def apply_migrations(
    database_url: str,
    *,
    directory: Path = MIGRATIONS_DIR,
) -> list[Migration]:
    """Apply pending migrations once, under a database-wide advisory lock."""

    migrations = load_migrations(directory)
    connection = await asyncpg.connect(asyncpg_url(database_url))
    applied_now: list[Migration] = []
    try:
        await connection.execute("SELECT pg_advisory_lock(hashtext($1))", MIGRATION_LOCK_KEY)
        await connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {MIGRATION_TABLE} (
                version text PRIMARY KEY,
                checksum char(64) NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        applied_rows = await connection.fetch(
            f"SELECT version, checksum FROM {MIGRATION_TABLE} ORDER BY version"
        )
        applied = {str(row["version"]): str(row["checksum"]) for row in applied_rows}

        for migration in migrations:
            previous_checksum = applied.get(migration.version)
            if previous_checksum is not None:
                if previous_checksum != migration.checksum:
                    raise RuntimeError(
                        f"Applied migration was changed: {migration.path.name}. "
                        "Create a new migration instead."
                    )
                continue
            async with connection.transaction():
                await connection.execute(migration.sql)
                await connection.execute(
                    f"INSERT INTO {MIGRATION_TABLE} (version, checksum) VALUES ($1, $2)",
                    migration.version,
                    migration.checksum,
                )
            applied_now.append(migration)
    finally:
        try:
            await connection.execute("SELECT pg_advisory_unlock(hashtext($1))", MIGRATION_LOCK_KEY)
        finally:
            await connection.close()
    return applied_now


async def seed_demo_data(database_url: str, *, path: Path = DEMO_DATA_PATH) -> None:
    """Load the idempotent local-demo seed data after the schema is migrated."""

    sql = path.read_text(encoding="utf-8")
    connection = await asyncpg.connect(asyncpg_url(database_url))
    try:
        await connection.execute(sql)
    finally:
        await connection.close()
