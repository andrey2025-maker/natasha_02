-- Initial schema for Cargo_Omsk55 bots

CREATE TABLE IF NOT EXISTS user_profiles (
    id              BIGSERIAL PRIMARY KEY,
    code            VARCHAR(16) NOT NULL UNIQUE,
    name            TEXT NOT NULL DEFAULT '',
    phone           TEXT NOT NULL DEFAULT '',
    city            TEXT NOT NULL DEFAULT '',
    has_passport    BOOLEAN NOT NULL DEFAULT FALSE,
    telegram_user_id BIGINT UNIQUE,
    vk_user_id      BIGINT UNIQUE,
    is_blocked_by_admin BOOLEAN NOT NULL DEFAULT FALSE,
    blocked_bot     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_activity_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_profiles_telegram ON user_profiles (telegram_user_id);
CREATE INDEX IF NOT EXISTS idx_user_profiles_vk ON user_profiles (vk_user_id);

CREATE TABLE IF NOT EXISTS reserved_codes (
    code VARCHAR(16) PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS user_sessions (
    id               BIGSERIAL PRIMARY KEY,
    platform         VARCHAR(16) NOT NULL,
    platform_user_id BIGINT NOT NULL,
    state            VARCHAR(64) NOT NULL DEFAULT 'idle',
    state_data       JSONB NOT NULL DEFAULT '{}',
    user_profile_id  BIGINT REFERENCES user_profiles(id) ON DELETE SET NULL,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (platform, platform_user_id)
);

CREATE TABLE IF NOT EXISTS sync_requests (
    id                BIGSERIAL PRIMARY KEY,
    profile_code      VARCHAR(16) NOT NULL,
    from_platform     VARCHAR(16) NOT NULL,
    to_platform       VARCHAR(16) NOT NULL,
    verification_code VARCHAR(16) NOT NULL,
    state             VARCHAR(16) NOT NULL DEFAULT 'pending',
    expires_at        TIMESTAMPTZ NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sync_requests_profile_code ON sync_requests (profile_code, created_at DESC);

CREATE TABLE IF NOT EXISTS buyout_orders (
    id               BIGSERIAL PRIMARY KEY,
    user_profile_id  BIGINT NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    order_number     VARCHAR(32) NOT NULL UNIQUE,
    flow_type        VARCHAR(16) NOT NULL DEFAULT 'buyout',
    status           VARCHAR(32) NOT NULL DEFAULT 'pending',
    product_url      TEXT NOT NULL DEFAULT '',
    quantity_text    TEXT NOT NULL DEFAULT '',
    media_group_id   TEXT,
    media_storage_chat_id BIGINT,
    media_storage_topic_id BIGINT,
    media_storage_message_id BIGINT,
    media_vk_attachment TEXT,
    price_rub        INTEGER,
    track_number     TEXT,
    manager_comment  TEXT NOT NULL DEFAULT '',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_buyout_orders_profile ON buyout_orders (user_profile_id, created_at DESC);
ALTER TABLE buyout_orders ADD COLUMN IF NOT EXISTS track_number TEXT;
ALTER TABLE buyout_orders ADD COLUMN IF NOT EXISTS media_storage_chat_id BIGINT;
ALTER TABLE buyout_orders ADD COLUMN IF NOT EXISTS media_storage_topic_id BIGINT;
ALTER TABLE buyout_orders ADD COLUMN IF NOT EXISTS media_storage_message_id BIGINT;
ALTER TABLE buyout_orders ADD COLUMN IF NOT EXISTS media_vk_attachment TEXT;

CREATE TABLE IF NOT EXISTS outbound_messages (
    id               BIGSERIAL PRIMARY KEY,
    platform         VARCHAR(16) NOT NULL,
    platform_user_id BIGINT NOT NULL,
    message_type     VARCHAR(64) NOT NULL,
    payload          JSONB NOT NULL DEFAULT '{}',
    status           VARCHAR(16) NOT NULL DEFAULT 'pending',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_outbound_messages_status ON outbound_messages (status, created_at);

CREATE TABLE IF NOT EXISTS admin_users (
    telegram_user_id BIGINT PRIMARY KEY,
    added_by BIGINT NOT NULL,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS faq_sections (
    id BIGSERIAL PRIMARY KEY,
    parent_id BIGINT REFERENCES faq_sections(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    content_text TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_faq_sections_parent ON faq_sections(parent_id, sort_order, id);

CREATE TABLE IF NOT EXISTS buyout_order_status_history (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES buyout_orders(id) ON DELETE CASCADE,
    previous_status VARCHAR(32),
    new_status VARCHAR(32) NOT NULL,
    changed_by_platform VARCHAR(16) NOT NULL,
    changed_by_user_id BIGINT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_buyout_order_status_history_order ON buyout_order_status_history(order_id, changed_at DESC);

CREATE TABLE IF NOT EXISTS order_media (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES buyout_orders(id) ON DELETE CASCADE,
    platform VARCHAR(16) NOT NULL,
    media_type VARCHAR(32) NOT NULL DEFAULT 'unknown',
    tg_chat_id BIGINT,
    tg_topic_id BIGINT,
    tg_message_id BIGINT,
    tg_file_id TEXT,
    vk_attachment TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_order_media_order ON order_media(order_id, created_at);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
