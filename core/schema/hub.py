from __future__ import annotations

"""中心端建表定义（hub schema v2）。

账号模型从 3.x 的单表拆成四层：
    accounts            身份（用户名、昵称、邮箱、角色、状态、时间戳、扩展元数据）
    account_credentials 凭据（口令算法/迭代/盐/哈希、API Token）
    account_profiles    档案（简介、技能、头像、头像主题、可见性）
    accounts_fts        检索（用户名与昵称的全文索引）

拆分理由：身份是长期稳定的主键，凭据会换算法、档案会长大。
三者分开后，改口令策略不必动身份表，账号量大时也能各走各的索引。
"""

HUB_SCHEMA_VERSION = 3

HUB_SCHEMA_SQL = r"""
-- 身份：只放"这个人是谁"，不放任何凭据
CREATE TABLE IF NOT EXISTS accounts(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL COLLATE NOCASE,
    display_name TEXT NOT NULL DEFAULT '',
    email TEXT COLLATE NOCASE,
    role TEXT NOT NULL DEFAULT 'member' CHECK(role IN ('admin','member')),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled','pending')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT,
    last_seen_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    -- 3.x 的 active 列保留为派生列：读旧查询不用改，写入只认 status
    active INTEGER GENERATED ALWAYS AS (CASE WHEN status='active' THEN 1 ELSE 0 END) VIRTUAL,
    UNIQUE(username),
    UNIQUE(email)
);

-- 凭据：与身份 1:1 分离，换算法或加二次验证时不动身份表
CREATE TABLE IF NOT EXISTS account_credentials(
    account_id INTEGER PRIMARY KEY,
    password_algo TEXT NOT NULL DEFAULT 'pbkdf2_sha256',
    password_iterations INTEGER NOT NULL DEFAULT 310000,
    password_salt TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    api_token TEXT UNIQUE,
    token_rotated_at TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
);

-- 档案：可公开、可被成员编辑的部分
CREATE TABLE IF NOT EXISTS account_profiles(
    account_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    bio TEXT NOT NULL DEFAULT '',
    skills TEXT NOT NULL DEFAULT '',
    capabilities TEXT NOT NULL DEFAULT '',
    goals TEXT NOT NULL DEFAULT '',
    avatar_symbol TEXT NOT NULL DEFAULT '道',
    theme_json TEXT NOT NULL DEFAULT '{}',
    public INTEGER NOT NULL DEFAULT 1,
    revision INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
);

-- 用户名/昵称检索：账号量大时走索引，不用 LIKE 全表
CREATE VIRTUAL TABLE IF NOT EXISTS accounts_fts USING fts5(
    username, display_name, content='accounts', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS accounts_fts_ai AFTER INSERT ON accounts BEGIN
    INSERT INTO accounts_fts(rowid, username, display_name) VALUES (new.id, new.username, new.display_name);
END;
CREATE TRIGGER IF NOT EXISTS accounts_fts_ad AFTER DELETE ON accounts BEGIN
    INSERT INTO accounts_fts(accounts_fts, rowid, username, display_name) VALUES('delete', old.id, old.username, old.display_name);
END;
CREATE TRIGGER IF NOT EXISTS accounts_fts_au AFTER UPDATE ON accounts BEGIN
    INSERT INTO accounts_fts(accounts_fts, rowid, username, display_name) VALUES('delete', old.id, old.username, old.display_name);
    INSERT INTO accounts_fts(rowid, username, display_name) VALUES (new.id, new.username, new.display_name);
END;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
            CREATE TABLE IF NOT EXISTS hub_asset_transactions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                asset_key TEXT NOT NULL,
                amount INTEGER NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                event_uuid TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES accounts(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS hub_inventory(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                item_key TEXT NOT NULL,
                item_type TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                level INTEGER NOT NULL DEFAULT 1,
                equipped INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id,item_key),
                FOREIGN KEY(user_id) REFERENCES accounts(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS hub_sync_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                event_uuid TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                rewarded INTEGER NOT NULL DEFAULT 0,
                result_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES accounts(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS hub_invites(
                code TEXT PRIMARY KEY,
                uses_remaining INTEGER NOT NULL DEFAULT 1,
                expires_at TEXT,
                created_by INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY(created_by) REFERENCES accounts(id) ON DELETE SET NULL
            );
            CREATE TABLE IF NOT EXISTS hub_releases(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                file_path TEXT NOT NULL,
                file_size INTEGER NOT NULL DEFAULT 0,
                sha256 TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_by INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY(created_by) REFERENCES accounts(id) ON DELETE SET NULL
            );
            CREATE TABLE IF NOT EXISTS hub_welfare_grants(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient_id INTEGER NOT NULL,
                granted_by INTEGER NOT NULL,
                xp INTEGER NOT NULL DEFAULT 0,
                spirit_stone INTEGER NOT NULL DEFAULT 0,
                spirit_wood INTEGER NOT NULL DEFAULT 0,
                mystic_iron INTEGER NOT NULL DEFAULT 0,
                star_sand INTEGER NOT NULL DEFAULT 0,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                claimed_at TEXT,
                FOREIGN KEY(recipient_id) REFERENCES accounts(id) ON DELETE CASCADE,
                FOREIGN KEY(granted_by) REFERENCES accounts(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS hub_resource_cards(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                visibility TEXT NOT NULL DEFAULT 'team',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES accounts(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS hub_forum_posts(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES accounts(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS hub_forum_replies(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(post_id) REFERENCES hub_forum_posts(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES accounts(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS hub_chat_messages(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES accounts(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS hub_social_transfers(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER NOT NULL,
                recipient_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                offer_asset TEXT NOT NULL,
                offer_amount INTEGER NOT NULL,
                request_asset TEXT NOT NULL DEFAULT '',
                request_amount INTEGER NOT NULL DEFAULT 0,
                note TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY(sender_id) REFERENCES accounts(id) ON DELETE CASCADE,
                FOREIGN KEY(recipient_id) REFERENCES accounts(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS hub_feedback(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER,
                kind TEXT NOT NULL DEFAULT 'bug',
                content TEXT NOT NULL,
                contact TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE SET NULL
            );
            CREATE TABLE IF NOT EXISTS hub_audit_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                ip TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES accounts(id) ON DELETE SET NULL
            );
            CREATE TABLE IF NOT EXISTS hub_settings(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_hub_events_user_created ON hub_sync_events(user_id,created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_hub_assets_user_created ON hub_asset_transactions(user_id,created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_hub_releases_created ON hub_releases(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_hub_welfare_recipient ON hub_welfare_grants(recipient_id,created_at DESC);

CREATE INDEX IF NOT EXISTS idx_accounts_role_status ON accounts(role, status);
CREATE INDEX IF NOT EXISTS idx_accounts_created ON accounts(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_accounts_role_created ON accounts(role, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_accounts_last_seen ON accounts(last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_credentials_token ON account_credentials(api_token);
"""
