-- Upgrade deployments created before the taxonomy/profile/session contracts were renamed.
-- The old profile tables are retained under legacy_* names because the removed
-- attribute-preference and session-interest fields have no lossless target in the new model.

DO $$
BEGIN
    IF to_regclass('category_groups') IS NOT NULL THEN
        INSERT INTO category_l1 (id, code, created_at, updated_at)
        SELECT id, code, created_at, updated_at
        FROM category_groups
        ON CONFLICT DO NOTHING;
    END IF;

    IF to_regclass('categories') IS NOT NULL THEN
        INSERT INTO category_l1 (code)
        SELECT DISTINCT category_l1
        FROM categories
        ON CONFLICT (code) DO NOTHING;

        INSERT INTO category_l2 (
            id, category_l1_id, category_l1, category_l2,
            required_slots, optional_slots, created_at, updated_at
        )
        SELECT
            legacy.id,
            l1.id,
            legacy.category_l1,
            legacy.category_l2,
            legacy.required_slots,
            legacy.optional_slots,
            legacy.created_at,
            legacy.updated_at
        FROM categories AS legacy
        JOIN category_l1 AS l1 ON l1.code = legacy.category_l1
        ON CONFLICT DO NOTHING;

        UPDATE category_l2 AS target
        SET category_l1_id = l1.id,
            category_l1 = legacy.category_l1,
            required_slots = legacy.required_slots,
            optional_slots = legacy.optional_slots,
            updated_at = legacy.updated_at
        FROM categories AS legacy
        JOIN category_l1 AS l1 ON l1.code = legacy.category_l1
        WHERE target.category_l2 = legacy.category_l2;
    END IF;

    IF to_regclass('category_slots') IS NOT NULL THEN
        ALTER TABLE category_slots DROP CONSTRAINT IF EXISTS category_slots_category_id_fkey;
        ALTER TABLE category_slots DROP CONSTRAINT IF EXISTS category_slots_category_l2_fk;

        IF to_regclass('categories') IS NOT NULL THEN
            UPDATE category_slots AS slots
            SET category_id = target.id
            FROM categories AS legacy
            JOIN category_l2 AS target ON target.category_l2 = legacy.category_l2
            WHERE slots.category_id = legacy.id;
        END IF;

        ALTER TABLE category_slots
            ADD CONSTRAINT category_slots_category_l2_fk
            FOREIGN KEY (category_id)
            REFERENCES category_l2(id)
            ON DELETE CASCADE;
    END IF;
END;
$$;

DROP TABLE IF EXISTS categories;
DROP TABLE IF EXISTS category_groups;

DO $$
BEGIN
    IF to_regclass('user_static_profiles') IS NOT NULL THEN
        EXECUTE $sql$
            INSERT INTO user_profile_static (user_id, budget_band, locale, updated_at)
            SELECT
                user_id,
                CASE
                    WHEN price_max IS NULL THEN NULL
                    WHEN price_max < 500 THEN 'entry'
                    WHEN price_max <= 1500 THEN 'mid'
                    ELSE 'premium'
                END,
                'zh_cn',
                COALESCE(updated_at, last_event_at, CURRENT_TIMESTAMP)
            FROM user_static_profiles
            ON CONFLICT (user_id) DO NOTHING
        $sql$;

        EXECUTE $sql$
            INSERT INTO user_profile_dynamic (
                user_id, category_affinity, brand_affinity,
                recent_viewed, recent_purchased, updated_at
            )
            SELECT
                user_id,
                category_scores,
                brand_scores,
                ARRAY[]::uuid[],
                ARRAY[]::uuid[],
                COALESCE(updated_at, last_event_at, CURRENT_TIMESTAMP)
            FROM user_static_profiles
            ON CONFLICT (user_id) DO NOTHING
        $sql$;
    END IF;

    IF to_regclass('user_dynamic_profiles') IS NOT NULL THEN
        EXECUTE $sql$
            INSERT INTO user_profile_dynamic (
                user_id, category_affinity, brand_affinity,
                recent_viewed, recent_purchased, updated_at
            )
            SELECT
                user_id,
                category_scores,
                '{}'::jsonb,
                ARRAY(
                    SELECT product_id::uuid
                    FROM jsonb_each_text(product_scores) AS score(product_id, value)
                    WHERE product_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                    ORDER BY CASE
                        WHEN value ~ '^-?[0-9]+(\.[0-9]+)?$' THEN value::numeric
                        ELSE 0
                    END DESC
                ),
                ARRAY[]::uuid[],
                COALESCE(updated_at, last_event_at, CURRENT_TIMESTAMP)
            FROM user_dynamic_profiles
            ON CONFLICT (user_id) DO UPDATE SET
                category_affinity = EXCLUDED.category_affinity,
                recent_viewed = EXCLUDED.recent_viewed,
                updated_at = GREATEST(user_profile_dynamic.updated_at, EXCLUDED.updated_at)
        $sql$;
    END IF;

    IF to_regclass('user_static_profiles') IS NOT NULL THEN
        EXECUTE $sql$
            UPDATE user_profile_dynamic AS target
            SET brand_affinity = legacy.brand_scores,
                updated_at = GREATEST(target.updated_at, legacy.updated_at)
            FROM user_static_profiles AS legacy
            WHERE target.user_id = legacy.user_id
              AND target.brand_affinity = '{}'::jsonb
              AND legacy.brand_scores <> '{}'::jsonb
        $sql$;
    END IF;

    IF to_regclass('user_static_profiles') IS NOT NULL
       AND to_regclass('legacy_user_static_profiles') IS NULL THEN
        EXECUTE 'ALTER TABLE user_static_profiles RENAME TO legacy_user_static_profiles';
    END IF;
    IF to_regclass('user_dynamic_profiles') IS NOT NULL
       AND to_regclass('legacy_user_dynamic_profiles') IS NULL THEN
        EXECUTE 'ALTER TABLE user_dynamic_profiles RENAME TO legacy_user_dynamic_profiles';
    END IF;
END;
$$;
