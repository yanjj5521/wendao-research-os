from __future__ import annotations

import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from core import accounts
from core import migrations as migration_engine
from core.migrations import HUB_MIGRATIONS
from core.schema import HUB_SCHEMA_SQL, HUB_SCHEMA_VERSION
from core.runtime_paths import INSTANCE_DIR, STORAGE_ROOT
from core.version import APP_VERSION

HUB_DB_PATH = INSTANCE_DIR / "hub.db"
HUB_BACKUP_DIR = STORAGE_ROOT / "hub_backups"
HUB_RELEASE_DIR = STORAGE_ROOT / "hub_releases"
HUB_SECRET_PATH = INSTANCE_DIR / "hub_secret.txt"
HUB_ADMIN_PATH = INSTANCE_DIR / "HUB_ADMIN_CREDENTIALS.txt"

# These are deliberately only the light-weight, shared assets of a 同行会.
# They are not a mirror of a member's private local research database.
ASSET_KEYS = ("cultivation", "spirit_stone", "spirit_wood", "mystic_iron", "star_sand")
WELFARE_SINGLE_LIMITS = {
    "xp": 60,
    "spirit_stone": 50,
    "spirit_wood": 6,
    "mystic_iron": 6,
    "star_sand": 6,
}
WELFARE_DAILY_LIMITS = {
    "grants": 3,
    "xp": 120,
    "spirit_stone": 100,
    "spirit_wood": 12,
    "mystic_iron": 12,
    "star_sand": 12,
}


def _restrict_private_file(path: Path) -> None:
    """Use owner-only POSIX permissions where the host filesystem supports them."""
    try:
        path.chmod(0o600)
    except OSError:
        pass


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def connect_hub() -> sqlite3.Connection:
    HUB_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(HUB_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


@contextmanager
def hub_transaction() -> Iterable[sqlite3.Connection]:
    conn = connect_hub()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def hub_secret() -> str:
    HUB_SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if HUB_SECRET_PATH.exists():
        return HUB_SECRET_PATH.read_text(encoding="utf-8").strip()
    value = secrets.token_urlsafe(48)
    HUB_SECRET_PATH.write_text(value, encoding="utf-8")
    _restrict_private_file(HUB_SECRET_PATH)
    return value


def create_user(
    conn: sqlite3.Connection,
    username: str,
    password: str,
    display_name: str,
    role: str = "member",
    email: str | None = None,
) -> tuple[int, str]:
    """创建账号并发放联机开宗礼包（身份逻辑在 core.accounts）。"""
    user_id, token = accounts.create_account(conn, username, password, display_name, role, email)
    ts = now_iso()
    for key, amount in {"cultivation": 100, "spirit_stone": 12, "spirit_wood": 4, "mystic_iron": 2, "star_sand": 2}.items():
        conn.execute(
            "INSERT INTO hub_asset_transactions(user_id,asset_key,amount,reason,created_at) VALUES (?,?,?,?,?)",
            (user_id, key, amount, "联机开宗礼包", ts),
        )
    return user_id, token


def balances(conn: sqlite3.Connection, user_id: int) -> dict[str, int]:
    result = {key: 0 for key in ASSET_KEYS}
    for row in conn.execute(
        "SELECT asset_key,COALESCE(SUM(amount),0) amount FROM hub_asset_transactions WHERE user_id=? GROUP BY asset_key",
        (user_id,),
    ):
        result[row["asset_key"]] = int(row["amount"])
    return result


def transact_asset(conn: sqlite3.Connection, user_id: int, asset_key: str, amount: int, reason: str, event_uuid: str | None = None) -> None:
    if asset_key not in ASSET_KEYS:
        raise ValueError("未知资产")
    if amount < 0 and balances(conn, user_id).get(asset_key, 0) + amount < 0:
        raise ValueError("资产不足")
    conn.execute(
        "INSERT INTO hub_asset_transactions(user_id,asset_key,amount,reason,event_uuid,created_at) VALUES (?,?,?,?,?,?)",
        (user_id, asset_key, int(amount), reason, event_uuid, now_iso()),
    )


def normalize_welfare_values(values: dict[str, object]) -> dict[str, int]:
    result: dict[str, int] = {}
    for key, upper in WELFARE_SINGLE_LIMITS.items():
        try:
            amount = int(values.get(key, 0) or 0)
        except (TypeError, ValueError):
            amount = 0
        if amount < 0 or amount > upper:
            raise ValueError(f"{key} 单次只能发放 0—{upper}。")
        result[key] = amount
    if not any(result.values()):
        raise ValueError("至少填写一项福利。")
    return result


def enforce_welfare_daily_limits(conn: sqlite3.Connection, recipient_id: int, values: dict[str, int]) -> None:
    today = datetime.now().astimezone().date().isoformat()
    row = conn.execute(
        """SELECT COUNT(*) grants,COALESCE(SUM(xp),0) xp,COALESCE(SUM(spirit_stone),0) spirit_stone,
                  COALESCE(SUM(spirit_wood),0) spirit_wood,COALESCE(SUM(mystic_iron),0) mystic_iron,
                  COALESCE(SUM(star_sand),0) star_sand
           FROM hub_welfare_grants WHERE recipient_id=? AND substr(created_at,1,10)=?""",
        (recipient_id, today),
    ).fetchone()
    if int(row["grants"] or 0) >= WELFARE_DAILY_LIMITS["grants"]:
        raise ValueError("该成员今日已达到 3 次福利上限。")
    for key, upper in WELFARE_DAILY_LIMITS.items():
        if key == "grants":
            continue
        if int(row[key] or 0) + int(values[key]) > upper:
            raise ValueError(f"该成员今日 {key} 福利上限为 {upper}。")


def backup_hub_db(keep: int = 14) -> Path:
    HUB_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = HUB_BACKUP_DIR / f"hub_{stamp}.db"
    source = connect_hub()
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    backups = sorted(HUB_BACKUP_DIR.glob("hub_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[keep:]:
        old.unlink(missing_ok=True)
    return target


def init_hub_db() -> None:
    HUB_RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    HUB_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    with hub_transaction() as conn:
        conn.executescript(HUB_SCHEMA_SQL)
        has_ledger = bool(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
        )
        if not has_ledger:
            raise RuntimeError(
                "hub.db 不是本版创建的（缺少 schema_migrations）。3.x 中心库与本版账号模型不兼容，"
                "请把旧库移走后重启，让程序重新建库。"
            )
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version > HUB_SCHEMA_VERSION:
            raise RuntimeError(
                f"中心库结构版本 v{version} 比当前程序（v{HUB_SCHEMA_VERSION}）更新，请先升级程序。"
            )
        if version < HUB_SCHEMA_VERSION:
            snapshot_dir = INSTANCE_DIR / "migration_snapshots"
            try:
                migration_engine.apply_migrations(
                    conn,
                    label="hub",
                    db_path=HUB_DB_PATH,
                    snapshot_dir=snapshot_dir,
                    migrations=HUB_MIGRATIONS,
                    current_version=version,
                    target_version=HUB_SCHEMA_VERSION,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"中心库升级失败，已回滚到 v{version}；迁移前快照在 {snapshot_dir}。原因：{exc}"
                ) from exc
        migration_engine.ensure_base_record(conn, HUB_SCHEMA_VERSION, "initial hub schema")
        # 新建库也应把版本号落到库头，否则下次 init 的迁移基线永远从 0 开始。
        conn.execute(f"PRAGMA user_version = {HUB_SCHEMA_VERSION}")
        defaults = {
            "site_name": "科研系统 · 同行会",
            "version": APP_VERSION,
            "registration_mode": "invite",
            "max_members": "10",
        }
        for key, value in defaults.items():
            conn.execute("INSERT OR IGNORE INTO hub_settings(key,value) VALUES (?,?)", (key, value))
        conn.execute(
            "UPDATE hub_settings SET value=? WHERE key='version'",
            (APP_VERSION,),
        )
        # Existing v3.5 team centres receive one small, virtual social balance.
        # It is intentionally separate from local XP and never uploads research files.
        for row in conn.execute("SELECT id FROM accounts WHERE active=1"):
            user_id = int(row["id"])
            has_cultivation = conn.execute(
                "SELECT 1 FROM hub_asset_transactions WHERE user_id=? AND asset_key='cultivation' LIMIT 1",
                (user_id,),
            ).fetchone()
            if not has_cultivation:
                conn.execute(
                    "INSERT INTO hub_asset_transactions(user_id,asset_key,amount,reason,created_at) VALUES (?,?,?,?,?)",
                    (user_id, "cultivation", 100, "同行会社交启用礼包", now_iso()),
                )
        conn.execute(
            """
            UPDATE hub_settings SET value='科研系统 · 同行会'
            WHERE key='site_name' AND trim(value)='问道科研 · 同行会'
            """
        )
        admin = conn.execute("SELECT id FROM accounts WHERE role='admin' LIMIT 1").fetchone()
        if not admin:
            password = secrets.token_urlsafe(10)
            user_id, token = create_user(conn, "admin", password, "洞府主人", role="admin")
            HUB_ADMIN_PATH.write_text(
                f"科研系统 v{APP_VERSION} 轻量联机中心管理员凭据\n"
                "================================\n"
                f"用户名: admin\n密码: {password}\nAPI Token: {token}\n\n"
                "首次登录后请立即修改密码。修改成功后系统会销毁这份一次性凭据文件；\n"
                "API Token 仍可在“我的联机主页”查看或重新生成。\n"
                "开发者留存：中心数据库 instance/hub.db（每日自动备份）；\n"
                "全部账号可在管理控制台导出 CSV；管理动作均写入审计日志。\n",
                encoding="utf-8",
            )
            _restrict_private_file(HUB_ADMIN_PATH)
            invite = secrets.token_urlsafe(8)
            conn.execute(
                "INSERT INTO hub_invites(code,uses_remaining,expires_at,created_by,created_at) VALUES (?,?,?,?,?)",
                (invite, 9, (datetime.now().astimezone() + timedelta(days=30)).isoformat(timespec="seconds"), user_id, now_iso()),
            )


def get_user_by_token(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    return accounts.account_by_token(conn, token)


def get_hub_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM hub_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default

