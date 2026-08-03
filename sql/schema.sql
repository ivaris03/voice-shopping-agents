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

CREATE TABLE IF NOT EXISTS category_l1 (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code            varchar(100) NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT category_l1_code_not_blank CHECK (btrim(code) <> ''),
    CONSTRAINT category_l1_code_uq UNIQUE (code)
);

DROP TRIGGER IF EXISTS category_l1_set_updated_at ON category_l1;
CREATE TRIGGER category_l1_set_updated_at
BEFORE UPDATE ON category_l1
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS category_l2 (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    category_l1_id  uuid NOT NULL,
    category_l1     varchar(100) NOT NULL,
    category_l2     varchar(100) NOT NULL,
    required_slots  text[] NOT NULL DEFAULT ARRAY[]::text[],
    optional_slots  text[] NOT NULL DEFAULT ARRAY[]::text[],
    created_at      timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT category_l2_l1_not_blank CHECK (btrim(category_l1) <> ''),
    CONSTRAINT category_l2_code_not_blank CHECK (btrim(category_l2) <> ''),
    CONSTRAINT category_l2_code_uq UNIQUE (category_l2),
    CONSTRAINT category_l2_slots_disjoint CHECK (NOT (required_slots && optional_slots))
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'category_l2_category_l1_fk'
          AND conrelid = 'category_l2'::regclass
    ) THEN
        ALTER TABLE category_l2
            ADD CONSTRAINT category_l2_category_l1_fk
            FOREIGN KEY (category_l1_id)
            REFERENCES category_l1(id)
            ON DELETE RESTRICT;
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS category_l2_l1_idx ON category_l2 (category_l1, category_l2);

DROP TRIGGER IF EXISTS category_l2_set_updated_at ON category_l2;
CREATE TRIGGER category_l2_set_updated_at
BEFORE UPDATE ON category_l2
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS category_slots (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    category_id     uuid NOT NULL REFERENCES category_l2(id) ON DELETE CASCADE,
    key             varchar(100) NOT NULL,
    is_required     boolean NOT NULL,
    enum_values     jsonb NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT category_slots_key_format CHECK (key ~ '^[a-z][A-Za-z0-9]*$'),
    CONSTRAINT category_slots_enum_array CHECK (
        jsonb_typeof(enum_values) = 'array' AND jsonb_array_length(enum_values) > 0
    ),
    CONSTRAINT category_slots_category_key_uq UNIQUE (category_id, key)
);

CREATE INDEX IF NOT EXISTS category_slots_category_idx
    ON category_slots (category_id, is_required, created_at);

DROP TRIGGER IF EXISTS category_slots_set_updated_at ON category_slots;
CREATE TRIGGER category_slots_set_updated_at
BEFORE UPDATE ON category_slots
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

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

-- 每轮保存一份业务状态投影；LangGraph 的完整 State 由它自己的 checkpointer 管理，
-- 这里仅保存需要被业务层查询、审计或在无 checkpoint 时引导 Graph 的事实。
CREATE TABLE IF NOT EXISTS session_states (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id       uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn_id          uuid NOT NULL,
    state_version    integer NOT NULL DEFAULT 1,
    business_state   jsonb NOT NULL DEFAULT '{}'::jsonb,
    pending_order_id uuid REFERENCES orders(id) ON DELETE SET NULL,
    created_at       timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT session_states_version_check CHECK (state_version > 0),
    CONSTRAINT session_states_business_object_check
        CHECK (jsonb_typeof(business_state) = 'object'),
    CONSTRAINT session_states_session_turn_uq UNIQUE (session_id, turn_id)
);

-- Existing local databases may still have the pre-LangGraph-projection columns.
-- Keep schema.sql re-runnable while moving those databases to the new contract.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'session_states'
          AND column_name = 'workflow_state'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'session_states'
          AND column_name = 'business_state'
    ) THEN
        ALTER TABLE session_states RENAME COLUMN workflow_state TO business_state;
    ELSIF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'session_states'
          AND column_name = 'workflow_state'
    ) THEN
        ALTER TABLE session_states DROP COLUMN workflow_state;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'session_states'
          AND column_name = 'business_state'
    ) THEN
        ALTER TABLE session_states
            ADD COLUMN business_state jsonb NOT NULL DEFAULT '{}'::jsonb;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'session_states'
          AND column_name = 'state_version'
    ) THEN
        ALTER TABLE session_states
            ADD COLUMN state_version integer NOT NULL DEFAULT 1;
    END IF;

    ALTER TABLE session_states
        DROP COLUMN IF EXISTS user_profile_snapshot,
        DROP COLUMN IF EXISTS langgraph_checkpoint_id;
END;
$$;

ALTER TABLE session_states
    ALTER COLUMN business_state SET DEFAULT '{}'::jsonb,
    ALTER COLUMN business_state SET NOT NULL,
    ALTER COLUMN state_version SET DEFAULT 1,
    ALTER COLUMN state_version SET NOT NULL;

ALTER TABLE session_states
    DROP CONSTRAINT IF EXISTS session_states_workflow_object_check,
    DROP CONSTRAINT IF EXISTS session_states_profile_object_check,
    DROP CONSTRAINT IF EXISTS session_states_business_object_check,
    DROP CONSTRAINT IF EXISTS session_states_version_check;

ALTER TABLE session_states
    ADD CONSTRAINT session_states_version_check CHECK (state_version > 0),
    ADD CONSTRAINT session_states_business_object_check
        CHECK (jsonb_typeof(business_state) = 'object');

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
-- 五、用户画像：静态属性与动态行为
-- 用户和商品在本项目中使用 UUID，因此这里保留 UUID 类型；字段语义与
-- 业务画像设计保持一致。静态画像由用户资料、人工确认或会话结束时的画像收敛更新，
-- 动态画像由行为更新。
-- ============================================================================

CREATE TABLE IF NOT EXISTS user_profile_static (
    user_id         uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    gender          varchar(8),
    age             integer,
    city            varchar(32),
    height_cm       integer,
    weight_kg       integer,
    skin_type       varchar(16),
    tech_savvy      varchar(16),
    budget_band     varchar(16),
    locale          varchar(16) NOT NULL DEFAULT 'zh_cn',
    updated_at      timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT user_profile_static_age_check CHECK (age IS NULL OR age BETWEEN 0 AND 120),
    CONSTRAINT user_profile_static_height_check CHECK (
        height_cm IS NULL OR height_cm BETWEEN 50 AND 250
    ),
    CONSTRAINT user_profile_static_weight_check CHECK (
        weight_kg IS NULL OR weight_kg BETWEEN 10 AND 300
    )
);

DROP TRIGGER IF EXISTS user_profile_static_set_updated_at ON user_profile_static;
CREATE TRIGGER user_profile_static_set_updated_at
BEFORE UPDATE ON user_profile_static
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS user_profile_dynamic (
    user_id             uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    category_affinity   jsonb NOT NULL DEFAULT '{}'::jsonb,
    brand_affinity      jsonb NOT NULL DEFAULT '{}'::jsonb,
    recent_viewed       uuid[] NOT NULL DEFAULT ARRAY[]::uuid[],
    recent_purchased    uuid[] NOT NULL DEFAULT ARRAY[]::uuid[],
    price_sensitivity   numeric(3, 2),
    avg_order_amount    numeric(10, 2),
    updated_at          timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT user_profile_dynamic_category_object_check CHECK (
        jsonb_typeof(category_affinity) = 'object'
    ),
    CONSTRAINT user_profile_dynamic_brand_object_check CHECK (
        jsonb_typeof(brand_affinity) = 'object'
    ),
    CONSTRAINT user_profile_dynamic_price_sensitivity_check CHECK (
        price_sensitivity IS NULL OR price_sensitivity BETWEEN 0 AND 1
    ),
    CONSTRAINT user_profile_dynamic_avg_order_amount_check CHECK (
        avg_order_amount IS NULL OR avg_order_amount >= 0
    )
);

DROP TRIGGER IF EXISTS user_profile_dynamic_set_updated_at ON user_profile_dynamic;
CREATE TRIGGER user_profile_dynamic_set_updated_at
BEFORE UPDATE ON user_profile_dynamic
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMIT;
