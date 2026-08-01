-- 语音导购平台数据库结构
-- PostgreSQL 15 + pgvector
-- Database: "voice-shopping-agents"
-- 创建数据库：CREATE DATABASE "voice-shopping-agents";
-- 执行建表：psql -d "voice-shopping-agents" -f sql/schema.sql

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

-- 统一维护 updated_at，避免各业务模块重复实现。
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$;

-- ============================================================================
-- 一、基础域：用户、商家
-- ============================================================================

CREATE TABLE IF NOT EXISTS users (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email           text NOT NULL,
    password_hash   text NOT NULL,
    display_name    varchar(100) NOT NULL,
    phone           varchar(32),
    role            varchar(20) NOT NULL DEFAULT 'customer',
    status          varchar(20) NOT NULL DEFAULT 'active',
    created_at      timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT users_email_not_blank CHECK (btrim(email) <> ''),
    CONSTRAINT users_email_normalized CHECK (email = lower(email)),
    CONSTRAINT users_role_check CHECK (role IN ('customer', 'merchant', 'platform')),
    CONSTRAINT users_status_check CHECK (status IN ('active', 'disabled'))
);

CREATE UNIQUE INDEX IF NOT EXISTS users_email_uq ON users (email);

DROP TRIGGER IF EXISTS users_set_updated_at ON users;
CREATE TRIGGER users_set_updated_at
BEFORE UPDATE ON users
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- merchants 同时表示商家主体及其店铺；一个商家账号可以维护多个店铺。
CREATE TABLE IF NOT EXISTS merchants (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id   uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    name            varchar(150) NOT NULL,
    slug            varchar(100) NOT NULL,
    description     text,
    logo_url        text,
    contact_phone   varchar(32),
    is_enabled      boolean NOT NULL DEFAULT true,
    disabled_reason text,
    created_at      timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at      timestamptz,
    CONSTRAINT merchants_name_not_blank CHECK (btrim(name) <> ''),
    CONSTRAINT merchants_slug_format CHECK (slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'),
    CONSTRAINT merchants_disable_reason_check CHECK (is_enabled OR disabled_reason IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS merchants_active_slug_uq
    ON merchants (slug) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS merchants_owner_idx
    ON merchants (owner_user_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS merchants_visible_idx
    ON merchants (is_enabled, created_at DESC) WHERE deleted_at IS NULL;

DROP TRIGGER IF EXISTS merchants_set_updated_at ON merchants;
CREATE TRIGGER merchants_set_updated_at
BEFORE UPDATE ON merchants
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================================
-- 二、商品域：平台品类、槽位与商品
-- ============================================================================

CREATE TABLE IF NOT EXISTS categories (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    category_l1     varchar(100) NOT NULL,
    category_l2     varchar(100) NOT NULL,
    required_slots  text[] NOT NULL DEFAULT ARRAY[]::text[],
    optional_slots  text[] NOT NULL DEFAULT ARRAY[]::text[],
    created_at      timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT categories_l1_not_blank CHECK (btrim(category_l1) <> ''),
    CONSTRAINT categories_l2_not_blank CHECK (btrim(category_l2) <> ''),
    CONSTRAINT categories_l2_uq UNIQUE (category_l2),
    CONSTRAINT categories_slots_disjoint CHECK (NOT (required_slots && optional_slots))
);

CREATE INDEX IF NOT EXISTS categories_l1_idx ON categories (category_l1, category_l2);

DROP TRIGGER IF EXISTS categories_set_updated_at ON categories;
CREATE TRIGGER categories_set_updated_at
BEFORE UPDATE ON categories
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 清理上一版拆分的临时结构，品类配置只保留 categories 单表。
DROP TABLE IF EXISTS product_category_slots;
DROP TABLE IF EXISTS product_categories_l2;
DROP TABLE IF EXISTS product_categories_l1;

CREATE TABLE IF NOT EXISTS products (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id     uuid NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    sku             varchar(80) NOT NULL,
    name            varchar(200) NOT NULL,
    category_l1     varchar(100) NOT NULL,
    category_l2     varchar(100) NOT NULL,
    brand           varchar(100),
    description     text NOT NULL DEFAULT '',
    price           numeric(12, 2) NOT NULL,
    stock           integer NOT NULL DEFAULT 0,
    attributes      jsonb NOT NULL DEFAULT '{}'::jsonb,
    selling_points  text[] NOT NULL DEFAULT ARRAY[]::text[],
    image_urls      text[] NOT NULL DEFAULT ARRAY[]::text[],
    status          varchar(20) NOT NULL DEFAULT 'draft',
    -- qwen3.7-text-embedding 的应用输出需在入库前归一到 1024 维。
    embedding       vector(1024),
    created_at      timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at      timestamptz,
    CONSTRAINT products_sku_not_blank CHECK (btrim(sku) <> ''),
    CONSTRAINT products_name_not_blank CHECK (btrim(name) <> ''),
    CONSTRAINT products_category_l1_not_blank CHECK (btrim(category_l1) <> ''),
    CONSTRAINT products_category_l2_not_blank CHECK (btrim(category_l2) <> ''),
    CONSTRAINT products_price_check CHECK (price >= 0),
    CONSTRAINT products_stock_check CHECK (stock >= 0),
    CONSTRAINT products_attributes_object_check CHECK (jsonb_typeof(attributes) = 'object'),
    CONSTRAINT products_status_check CHECK (status IN ('draft', 'on_sale', 'off_sale')),
    CONSTRAINT products_id_merchant_uq UNIQUE (id, merchant_id)
);

-- 品类与槽位由平台动态维护；跨表规则由 API 在商品写入前校验。
ALTER TABLE products DROP CONSTRAINT IF EXISTS products_attributes_category_check;

CREATE UNIQUE INDEX IF NOT EXISTS products_active_sku_uq
    ON products (merchant_id, sku) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS products_browse_idx
    ON products (merchant_id, category_l1, category_l2, created_at DESC)
    WHERE deleted_at IS NULL AND status = 'on_sale';
CREATE INDEX IF NOT EXISTS products_brand_idx
    ON products (brand, category_l2)
    WHERE deleted_at IS NULL AND status = 'on_sale' AND brand IS NOT NULL;
CREATE INDEX IF NOT EXISTS products_attributes_gin_idx
    ON products USING gin (attributes jsonb_path_ops);
-- 向量召回不可用或需要按名称搜索时，使用 trigram 相似度作为降级链路。
CREATE INDEX IF NOT EXISTS products_name_trgm_idx
    ON products USING gin (name gin_trgm_ops)
    WHERE deleted_at IS NULL AND status = 'on_sale';
CREATE INDEX IF NOT EXISTS products_embedding_hnsw_idx
    ON products USING hnsw (embedding vector_cosine_ops)
    WHERE deleted_at IS NULL AND status = 'on_sale' AND embedding IS NOT NULL;

DROP TRIGGER IF EXISTS products_set_updated_at ON products;
CREATE TRIGGER products_set_updated_at
BEFORE UPDATE ON products
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================================
-- 三、订单域：订单
-- ============================================================================

CREATE TABLE IF NOT EXISTS orders (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    merchant_id         uuid NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    product_id          uuid NOT NULL,
    session_id          uuid,
    source_turn_id      uuid,
    idempotency_key     varchar(120) NOT NULL,
    status              varchar(20) NOT NULL DEFAULT 'pending',
    quantity            integer NOT NULL,
    unit_price          numeric(12, 2) NOT NULL,
    total_amount        numeric(14, 2) GENERATED ALWAYS AS (unit_price * quantity) STORED,
    merchant_snapshot   jsonb NOT NULL,
    product_snapshot    jsonb NOT NULL,
    failure_reason      text,
    expires_at          timestamptz NOT NULL DEFAULT (CURRENT_TIMESTAMP + interval '15 minutes'),
    confirmed_at        timestamptz,
    created_at          timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT orders_idempotency_key_not_blank CHECK (btrim(idempotency_key) <> ''),
    CONSTRAINT orders_status_check CHECK (status IN ('pending', 'success', 'fail')),
    CONSTRAINT orders_quantity_check CHECK (quantity > 0),
    CONSTRAINT orders_unit_price_check CHECK (unit_price >= 0),
    CONSTRAINT orders_merchant_snapshot_object_check CHECK (jsonb_typeof(merchant_snapshot) = 'object'),
    CONSTRAINT orders_product_snapshot_object_check CHECK (jsonb_typeof(product_snapshot) = 'object'),
    CONSTRAINT orders_expiry_check CHECK (expires_at = created_at + interval '15 minutes'),
    CONSTRAINT orders_confirmation_check CHECK (
        (status = 'success' AND confirmed_at IS NOT NULL)
        OR (status <> 'success' AND confirmed_at IS NULL)
    ),
    CONSTRAINT orders_idempotency_key_uq UNIQUE (idempotency_key),
    CONSTRAINT orders_product_merchant_fk FOREIGN KEY (product_id, merchant_id)
        REFERENCES products(id, merchant_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS orders_user_recent_idx ON orders (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS orders_merchant_recent_idx ON orders (merchant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS orders_pending_expiry_idx ON orders (expires_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS orders_session_idx ON orders (session_id) WHERE session_id IS NOT NULL;

DROP TRIGGER IF EXISTS orders_set_updated_at ON orders;
CREATE TRIGGER orders_set_updated_at
BEFORE UPDATE ON orders
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 已结束的订单不可重新打开或改成另一种终态。
CREATE OR REPLACE FUNCTION enforce_order_status_transition()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.status IN ('success', 'fail') AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'terminal order status % cannot transition to %', OLD.status, NEW.status;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS orders_enforce_status_transition ON orders;
CREATE TRIGGER orders_enforce_status_transition
BEFORE UPDATE OF status ON orders
FOR EACH ROW EXECUTE FUNCTION enforce_order_status_transition();

-- ============================================================================
-- 四、会话域：会话、会话状态、会话消息
-- ============================================================================

CREATE TABLE IF NOT EXISTS sessions (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status              varchar(20) NOT NULL DEFAULT 'active',
    conversation_summary text NOT NULL DEFAULT '',
    last_turn_id        uuid,
    started_at          timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_active_at      timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at            timestamptz,
    created_at          timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT sessions_status_check CHECK (status IN ('active', 'closed')),
    CONSTRAINT sessions_closed_at_check CHECK (
        (status = 'active' AND ended_at IS NULL)
        OR (status = 'closed' AND ended_at IS NOT NULL)
    ),
    CONSTRAINT sessions_id_user_uq UNIQUE (id, user_id)
);

CREATE INDEX IF NOT EXISTS sessions_user_recent_idx ON sessions (user_id, last_active_at DESC);

DROP TRIGGER IF EXISTS sessions_set_updated_at ON sessions;
CREATE TRIGGER sessions_set_updated_at
BEFORE UPDATE ON sessions
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 每轮保存一份可恢复的 ShoppingState；画像快照只读地固化在该轮记录中。
CREATE TABLE IF NOT EXISTS session_states (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id              uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn_id                 uuid NOT NULL,
    workflow_state          jsonb NOT NULL,
    user_profile_snapshot   jsonb NOT NULL DEFAULT '{}'::jsonb,
    pending_order_id        uuid REFERENCES orders(id) ON DELETE SET NULL,
    langgraph_checkpoint_id text,
    created_at              timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT session_states_workflow_object_check CHECK (jsonb_typeof(workflow_state) = 'object'),
    CONSTRAINT session_states_profile_object_check CHECK (jsonb_typeof(user_profile_snapshot) = 'object'),
    CONSTRAINT session_states_session_turn_uq UNIQUE (session_id, turn_id)
);

CREATE INDEX IF NOT EXISTS session_states_latest_idx
    ON session_states (session_id, created_at DESC);

DROP TRIGGER IF EXISTS session_states_set_updated_at ON session_states;
CREATE TRIGGER session_states_set_updated_at
BEFORE UPDATE ON session_states
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS session_messages (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn_id         uuid NOT NULL,
    seq             integer NOT NULL,
    role            varchar(20) NOT NULL,
    message_type    varchar(30) NOT NULL DEFAULT 'text',
    content         text NOT NULL,
    metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT session_messages_seq_check CHECK (seq >= 0),
    CONSTRAINT session_messages_role_check CHECK (role IN ('user', 'assistant', 'system')),
    CONSTRAINT session_messages_type_check CHECK (message_type IN ('text', 'transcript', 'product_cards', 'order', 'status')),
    CONSTRAINT session_messages_metadata_object_check CHECK (jsonb_typeof(metadata) = 'object'),
    CONSTRAINT session_messages_turn_seq_uq UNIQUE (session_id, turn_id, seq)
);

CREATE INDEX IF NOT EXISTS session_messages_recent_idx
    ON session_messages (session_id, created_at DESC);

-- orders 在订单域先创建；会话表就绪后再补充订单与用户会话的组合外键。
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'orders_session_user_fk'
          AND conrelid = 'orders'::regclass
    ) THEN
        ALTER TABLE orders
            ADD CONSTRAINT orders_session_user_fk
            FOREIGN KEY (session_id, user_id)
            REFERENCES sessions(id, user_id)
            ON DELETE RESTRICT;
    END IF;
END;
$$;

-- ============================================================================
-- 五、画像域：用户静态画像、用户动态画像
-- 点击和正式下单后，由 FastAPI 在业务事务中更新两张画像表。
-- ============================================================================

CREATE TABLE IF NOT EXISTS user_static_profiles (
    user_id             uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    category_scores     jsonb NOT NULL DEFAULT '{}'::jsonb,
    brand_scores        jsonb NOT NULL DEFAULT '{}'::jsonb,
    attribute_preferences jsonb NOT NULL DEFAULT '{}'::jsonb,
    price_min           numeric(12, 2),
    price_max           numeric(12, 2),
    version             bigint NOT NULL DEFAULT 1,
    last_event_at       timestamptz,
    created_at          timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT user_static_profiles_category_object_check CHECK (jsonb_typeof(category_scores) = 'object'),
    CONSTRAINT user_static_profiles_brand_object_check CHECK (jsonb_typeof(brand_scores) = 'object'),
    CONSTRAINT user_static_profiles_attributes_object_check CHECK (jsonb_typeof(attribute_preferences) = 'object'),
    CONSTRAINT user_static_profiles_price_check CHECK (
        (price_min IS NULL OR price_min >= 0)
        AND (price_max IS NULL OR price_max >= 0)
        AND (price_min IS NULL OR price_max IS NULL OR price_min <= price_max)
    ),
    CONSTRAINT user_static_profiles_version_check CHECK (version > 0)
);

DROP TRIGGER IF EXISTS user_static_profiles_set_updated_at ON user_static_profiles;
CREATE TRIGGER user_static_profiles_set_updated_at
BEFORE UPDATE ON user_static_profiles
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS user_dynamic_profiles (
    user_id             uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    category_scores     jsonb NOT NULL DEFAULT '{}'::jsonb,
    product_scores      jsonb NOT NULL DEFAULT '{}'::jsonb,
    session_interests   jsonb NOT NULL DEFAULT '{}'::jsonb,
    version             bigint NOT NULL DEFAULT 1,
    last_event_at       timestamptz,
    expires_at          timestamptz,
    created_at          timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT user_dynamic_profiles_category_object_check CHECK (jsonb_typeof(category_scores) = 'object'),
    CONSTRAINT user_dynamic_profiles_product_object_check CHECK (jsonb_typeof(product_scores) = 'object'),
    CONSTRAINT user_dynamic_profiles_interests_object_check CHECK (jsonb_typeof(session_interests) = 'object'),
    CONSTRAINT user_dynamic_profiles_version_check CHECK (version > 0)
);

CREATE INDEX IF NOT EXISTS user_dynamic_profiles_expiry_idx
    ON user_dynamic_profiles (expires_at) WHERE expires_at IS NOT NULL;

DROP TRIGGER IF EXISTS user_dynamic_profiles_set_updated_at ON user_dynamic_profiles;
CREATE TRIGGER user_dynamic_profiles_set_updated_at
BEFORE UPDATE ON user_dynamic_profiles
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMIT;
