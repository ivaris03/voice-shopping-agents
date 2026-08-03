import json
from uuid import UUID

import asyncpg
import pytest

from voice_shopping_api.core.migrations import MIGRATIONS_DIR, asyncpg_url

LEGACY_USER_ID = "90000000-0000-4000-8000-000000000001"
LEGACY_CATEGORY_ID = "92000000-0000-4000-8000-000000000001"


async def _seed_legacy_contract(connection: asyncpg.Connection) -> None:
    await connection.execute(
        """
        INSERT INTO users (id, email, password_hash, display_name)
        VALUES (
            '90000000-0000-4000-8000-000000000001',
            'migration-user@example.test',
            'legacy-password-hash',
            '迁移用户'
        );

        CREATE TABLE category_groups (
            id uuid PRIMARY KEY,
            code varchar(100) NOT NULL UNIQUE,
            created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE categories (
            id uuid PRIMARY KEY,
            category_l1 varchar(100) NOT NULL,
            category_l2 varchar(100) NOT NULL UNIQUE,
            required_slots text[] NOT NULL DEFAULT ARRAY[]::text[],
            optional_slots text[] NOT NULL DEFAULT ARRAY[]::text[],
            created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO category_groups (id, code)
        VALUES ('91000000-0000-4000-8000-000000000001', 'LEGACY_ELECTRONICS');
        INSERT INTO categories (id, category_l1, category_l2, required_slots)
        VALUES (
            '92000000-0000-4000-8000-000000000001',
            'LEGACY_ELECTRONICS',
            'LEGACY_HEADPHONES',
            ARRAY['form']
        );

        ALTER TABLE category_slots DROP CONSTRAINT IF EXISTS category_slots_category_id_fkey;
        ALTER TABLE category_slots DROP CONSTRAINT IF EXISTS category_slots_category_l2_fk;
        INSERT INTO category_slots (category_id, key, is_required, enum_values)
        VALUES (
            '92000000-0000-4000-8000-000000000001',
            'form',
            true,
            '["over-ear","in-ear"]'::jsonb
        );

        CREATE TABLE user_static_profiles (
            user_id uuid PRIMARY KEY REFERENCES users(id),
            category_scores jsonb NOT NULL DEFAULT '{}'::jsonb,
            brand_scores jsonb NOT NULL DEFAULT '{}'::jsonb,
            attribute_preferences jsonb NOT NULL DEFAULT '{}'::jsonb,
            price_min numeric(12, 2),
            price_max numeric(12, 2),
            last_event_at timestamptz,
            updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE user_dynamic_profiles (
            user_id uuid PRIMARY KEY REFERENCES users(id),
            category_scores jsonb NOT NULL DEFAULT '{}'::jsonb,
            product_scores jsonb NOT NULL DEFAULT '{}'::jsonb,
            session_interests jsonb NOT NULL DEFAULT '{}'::jsonb,
            last_event_at timestamptz,
            updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO user_static_profiles (
            user_id, category_scores, brand_scores, attribute_preferences, price_max
        ) VALUES (
            '90000000-0000-4000-8000-000000000001',
            '{"LEGACY_HEADPHONES":0.8}'::jsonb,
            '{"迁移品牌":0.6}'::jsonb,
            '{"noiseCancellation":0.9}'::jsonb,
            1800
        );
        INSERT INTO user_dynamic_profiles (
            user_id, category_scores, product_scores, session_interests
        )
        VALUES (
            '90000000-0000-4000-8000-000000000001',
            '{"LEGACY_HEADPHONES":0.95}'::jsonb,
            '{"20000000-0000-4000-8000-000000000001":0.7}'::jsonb,
            '{"useCase":"commute"}'::jsonb
        );
        """
    )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_legacy_catalog_and_profiles_are_upgraded_without_data_loss(
    e2e_engine,
    e2e_database_url: str,
) -> None:
    connection = await asyncpg.connect(asyncpg_url(e2e_database_url))
    try:
        await _seed_legacy_contract(connection)
        migration_path = MIGRATIONS_DIR / "20260804_migrate_legacy_catalog_and_profiles.sql"
        migration_sql = migration_path.read_text(encoding="utf-8")
        async with connection.transaction():
            await connection.execute(migration_sql)

        category = await connection.fetchrow(
            """
            SELECT l1.code AS category_l1, l2.required_slots
            FROM category_l2 AS l2
            JOIN category_l1 AS l1 ON l1.id = l2.category_l1_id
            WHERE l2.id = $1::uuid
            """,
            LEGACY_CATEGORY_ID,
        )
        assert dict(category) == {
            "category_l1": "LEGACY_ELECTRONICS",
            "required_slots": ["form"],
        }
        slot = await connection.fetchrow(
            "SELECT category_id, key, is_required FROM category_slots WHERE category_id = $1::uuid",
            LEGACY_CATEGORY_ID,
        )
        assert dict(slot) == {
            "category_id": UUID(LEGACY_CATEGORY_ID),
            "key": "form",
            "is_required": True,
        }

        static_profile = await connection.fetchrow(
            "SELECT budget_band FROM user_profile_static WHERE user_id = $1::uuid",
            LEGACY_USER_ID,
        )
        assert static_profile["budget_band"] == "premium"
        dynamic_profile = await connection.fetchrow(
            """
            SELECT category_affinity, brand_affinity, recent_viewed
            FROM user_profile_dynamic
            WHERE user_id = $1::uuid
            """,
            LEGACY_USER_ID,
        )
        assert json.loads(dynamic_profile["category_affinity"]) == {"LEGACY_HEADPHONES": 0.95}
        assert json.loads(dynamic_profile["brand_affinity"]) == {"迁移品牌": 0.6}
        assert [str(product_id) for product_id in dynamic_profile["recent_viewed"]] == [
            "20000000-0000-4000-8000-000000000001"
        ]
        assert await connection.fetchval("SELECT to_regclass('user_static_profiles')") is None
        assert await connection.fetchval("SELECT to_regclass('user_dynamic_profiles')") is None
        assert (
            await connection.fetchval("SELECT to_regclass('legacy_user_static_profiles')")
        ) is not None
        assert (
            await connection.fetchval("SELECT to_regclass('legacy_user_dynamic_profiles')")
        ) is not None
    finally:
        await connection.close()
