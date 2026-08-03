"""Apply the versioned SQL migrations to an application database."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from voice_shopping_api.core.config import get_settings  # noqa: E402
from voice_shopping_api.core.migrations import apply_migrations, seed_demo_data  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser(description="Apply Voice Shopping SQL migrations")
    parser.add_argument("--database-url", default=get_settings().database_url)
    parser.add_argument(
        "--seed-demo",
        action="store_true",
        help="load the idempotent local demo data after migrations",
    )
    args = parser.parse_args()

    applied = await apply_migrations(args.database_url)
    for migration in applied:
        print(f"Applied {migration.version}")
    if not applied:
        print("Database is already up to date")
    if args.seed_demo:
        await seed_demo_data(args.database_url)
        print("Demo data is ready")


if __name__ == "__main__":
    asyncio.run(main())
