from __future__ import annotations

from core.clock import now_iso
import json
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
from hub.invites import generate_invite_code
from core.runtime_paths import INSTANCE_DIR, STORAGE_ROOT
from core.version import APP_VERSION

HUB_DB_PATH = INSTANCE_DIR / "hub.db"
HUB_BACKUP_DIR = STORAGE_ROOT / "hub_backups"
HUB_RELEASE_DIR = STORAGE_ROOT / "hub_releases"
HUB_SECRET_PATH = INSTANCE_DIR / "hub_secret.txt"
HUB_ADMIN_PATH = INSTANCE_DIR / "HUB_ADMIN_CREDENTIALS.txt"

# These are deliberately only the light-weight, shared assets of a 同行会.
# They are not a mirror of a member's private local research database.
ASSET_KEYS = (
    "cultivation",
    "spirit_stone",
    "spirit_wood",
    "qi_grass",
    "dew_flower",
    "red_ganoderma",
    "azure_sword",
    "heart_mirror",
    "cosmos_bag",
    "mystic_iron",
    "star_sand",
    "primordial_stone",
)
WELFARE_SINGLE_LIMITS = {
    "xp": 200,
    "cultivation": 100,
    "spirit_stone": 150,
    "spirit_wood": 20,
    "qi_grass": 30,
    "dew_flower": 20,
    "red_ganoderma": 10,
    "azure_sword": 6,
    "heart_mirror": 6,
    "cosmos_bag": 2,
    "mystic_iron": 18,
    "star_sand": 18,
    "primordial_stone": 6,
}

WELFARE_LABELS = {
    "xp": "修为（同步）",
    "cultivation": "修为（资产）",
    "spirit_stone": "灵石",
    "spirit_wood": "灵木",
    "qi_grass": "青灵草",
    "dew_flower": "凝露花",
    "red_ganoderma": "赤芝",
    "azure_sword": "青锋剑",
    "heart_mirror": "心镜",
    "cosmos_bag": "乾坤袋",
    "mystic_iron": "玄铁",
    "star_sand": "星砂",
    "primordial_stone": "混元石",
}

# 每人每周额度：每周一自动刷新，数值在 [floor, cap] 间随机；floor 保证下限，
# 让成员每周都有一笔可观的成长资源（快速发展的基数）。
WELFARE_WEEKLY_FLOORS = {key: max(1, value * 3) for key, value in WELFARE_SINGLE_LIMITS.items()}
WELFARE_WEEKLY_CAPS = {key: value * 7 for key, value in WELFARE_SINGLE_LIMITS.items()}


def weekly_quota_key(now: datetime | None = None) -> str:
    """当前 ISO 周键，形如 2026-W45（周一为一周起点）。"""
    current = now or datetime.now().astimezone()
    year, week, _ = current.isocalendar()
    return f"{year}-W{week:02d}"


def week_start_iso(now: datetime | None = None) -> str:
    """当前 ISO 周的周一 00:00（本地时区），用于统计本周已发放。"""
    current = now or datetime.now().astimezone()
    year, week, _ = current.isocalendar()
    monday = datetime.fromisocalendar(year, week, 1)
    return monday.astimezone().isoformat(timespec="seconds")


REALM_LADDER: tuple[tuple[int, str, float], ...] = (
    (0, "练气", 1.00),
    (300, "筑基", 1.12),
    (900, "金丹", 1.25),
    (2200, "元婴", 1.40),
    (5000, "化神", 1.55),
    (10000, "炼虚", 1.70),
)


def welfare_scale(conn: sqlite3.Connection | None = None) -> dict[str, object]:
    """本周额度的两个乘数：管理员境界 × 成员人数。

    境界取中心侧累计修为（cultivation 资产），不依赖个人端 39 阶表；
    成员数只做温和加成（最多 +50%），避免"人一多就无限发"。
    """
    reader = conn or connect_hub()
    admin = reader.execute(
        """
        SELECT a.id, a.display_name,
               COALESCE((
                   SELECT SUM(t.amount) FROM hub_asset_transactions t
                   WHERE t.user_id=a.id AND t.asset_key='cultivation'
               ),0) AS cultivation
        FROM accounts a
        WHERE a.role='admin' AND a.active=1
        ORDER BY cultivation DESC, a.id
        LIMIT 1
        """
    ).fetchone()
    cultivation = int(admin["cultivation"] or 0) if admin else 0
    realm_name, realm_bonus = REALM_LADDER[0][1], REALM_LADDER[0][2]
    for threshold, name, factor in REALM_LADDER:
        if cultivation >= threshold:
            realm_name, realm_bonus = name, factor
    members = int(
        reader.execute(
            "SELECT COUNT(*) n FROM accounts WHERE role='member' AND active=1"
        ).fetchone()["n"]
    )
    member_bonus = 1.0 + min(0.5, max(0, members - 1) * 0.05)
    return {
        "admin_id": int(admin["id"]) if admin else 0,
        "admin_name": str(admin["display_name"]) if admin else "",
        "cultivation": cultivation,
        "realm_name": realm_name,
        "realm_bonus": round(realm_bonus, 2),
        "members": members,
        "member_bonus": round(member_bonus, 2),
        "total_bonus": round(realm_bonus * member_bonus, 2),
    }


def realm_label(cultivation: int | None) -> dict[str, object]:
    """把中心侧累计修为换算成境界，供成员列表/主页展示。

    阈值与福利额度共用 REALM_LADDER，避免出现两套境界标准。
    """
    value = max(0, int(cultivation or 0))
    name, floor, ceiling = REALM_LADDER[0][1], 0, None
    for index, (threshold, realm, _factor) in enumerate(REALM_LADDER):
        if value >= threshold:
            name = realm
            floor = threshold
            ceiling = REALM_LADDER[index + 1][0] if index + 1 < len(REALM_LADDER) else None
    span = (ceiling - floor) if ceiling else 0
    progress = 100 if not ceiling else int((value - floor) / max(1, span) * 100)
    return {
        "name": name,
        "cultivation": value,
        "next": ceiling,
        "progress": max(0, min(100, progress)),
    }


def display_realm(metadata_json: str | None, cultivation: int | None) -> dict[str, object]:
    """优先用个人端同步来的 39 阶境界名，没有快照时才退回中心 6 档换算。"""
    name = ""
    index = 0
    snapshot_cultivation = None
    try:
        meta = json.loads(metadata_json or "{}")
    except (json.JSONDecodeError, TypeError):
        meta = {}
    if isinstance(meta, dict):
        name = str(meta.get("realm_name", "")).strip()[:40]
        try:
            index = max(0, int(meta.get("realm_index", 0) or 0))
        except (TypeError, ValueError):
            index = 0
        try:
            snapshot_cultivation = int(meta.get("realm_cultivation", 0) or 0)
        except (TypeError, ValueError):
            snapshot_cultivation = None
    if name:
        return {
            "name": name,
            "index": index,
            "cultivation": snapshot_cultivation if snapshot_cultivation is not None else int(cultivation or 0),
            "source": "local",
            "next": None,
            "progress": 0,
        }
    return {**realm_label(cultivation), "source": "center", "index": 0}


def _load_quota_payload(raw: str) -> tuple[dict[str, int], dict[str, object]]:
    """兼容旧的扁平 dict 与新的 {"quotas":…, "meta":…}。"""
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}, {}
    if not isinstance(payload, dict):
        return {}, {}
    source = payload.get("quotas") if isinstance(payload.get("quotas"), dict) else payload
    try:
        quotas = {k: int(v) for k, v in source.items() if k in WELFARE_SINGLE_LIMITS}
    except (TypeError, ValueError):
        quotas = {}
    # 历史/手工写入的额度可能只包含部分键，缺的按本周下限补齐，避免校验时 KeyError。
    for key in WELFARE_SINGLE_LIMITS:
        quotas.setdefault(key, WELFARE_WEEKLY_FLOORS[key])
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    return quotas, meta


def weekly_quotas(conn: sqlite3.Connection | None = None, now: datetime | None = None) -> dict[str, int]:
    """本周每人额度：基准随机区间 × 管理员境界 × 成员人数。"""
    key = weekly_quota_key(now)
    reader = conn or connect_hub()
    row = reader.execute(
        "SELECT quotas_json FROM hub_welfare_week_quota WHERE week_key=?", (key,)
    ).fetchone()
    if row:
        quotas, _meta = _load_quota_payload(str(row["quotas_json"]))
        if quotas:
            return quotas

    def _generate(writer: sqlite3.Connection) -> dict[str, int]:
        """在**当前事务**里生成并落库；并发首写者以 INSERT OR IGNORE 后的库内值为准。"""
        row = writer.execute(
            "SELECT quotas_json FROM hub_welfare_week_quota WHERE week_key=?", (key,)
        ).fetchone()
        if row:
            quotas, _meta = _load_quota_payload(str(row["quotas_json"]))
            if quotas:
                return quotas
        scale = welfare_scale(writer)
        factor = float(scale["total_bonus"])
        generated = {
            k: max(
                1,
                int(
                    (
                        WELFARE_WEEKLY_FLOORS[k]
                        + secrets.randbelow(
                            max(1, WELFARE_WEEKLY_CAPS[k] - WELFARE_WEEKLY_FLOORS[k] + 1)
                        )
                    )
                    * factor
                ),
            )
            for k in WELFARE_SINGLE_LIMITS
        }
        payload = {
            "quotas": generated,
            "meta": {**scale, "week_key": key, "generated_at": now_iso()},
        }
        writer.execute(
            "INSERT OR IGNORE INTO hub_welfare_week_quota(week_key,quotas_json,created_at) VALUES (?,?,?)",
            (key, json.dumps(payload, ensure_ascii=False), now_iso()),
        )
        final = writer.execute(
            "SELECT quotas_json FROM hub_welfare_week_quota WHERE week_key=?", (key,)
        ).fetchone()
        quotas, _meta = _load_quota_payload(str(final["quotas_json"])) if final else (generated, {})
        return quotas or generated

    # 写入路径与读连接严格分离：读连接上的 INSERT 会把页面连接升级成写事务，
    # 一旦与其它写者相遇就互等至 busy_timeout。规则：
    #   * 调用方已开启写事务（conn.in_transaction）→ 复用该连接，同事务原子生成；
    #   * 否则（页面/只读连接）→ 用独立的 immediate 事务生成，绝不污染读连接。
    if conn is not None and conn.in_transaction:
        return _generate(conn)
    with hub_transaction(immediate=True) as writer:
        return _generate(writer)


def weekly_quota_meta(conn: sqlite3.Connection | None = None, now: datetime | None = None) -> dict[str, object]:
    """管理台展示用：本周额度是怎么算出来的。"""
    key = weekly_quota_key(now)
    reader = conn or connect_hub()
    row = reader.execute(
        "SELECT quotas_json FROM hub_welfare_week_quota WHERE week_key=?", (key,)
    ).fetchone()
    meta: dict[str, object] = {}
    if row:
        _quotas, meta = _load_quota_payload(str(row["quotas_json"]))
    if not meta:
        meta = {**welfare_scale(reader), "week_key": key, "generated_at": ""}
    return meta


def enforce_welfare_weekly_limits(
    conn: sqlite3.Connection, recipient_id: int, values: dict[str, int], now: datetime | None = None
) -> dict[str, int]:
    """每周额度校验：本周**累计**已发 + 本次 > 本周额度 时拒绝，并告知剩余。"""
    quotas = weekly_quotas(conn, now)
    start = week_start_iso(now)
    columns = ",".join(WELFARE_SINGLE_LIMITS)
    rows = conn.execute(
        f"""SELECT {columns} FROM hub_welfare_grants
            WHERE recipient_id=? AND created_at>=?""",
        (recipient_id, start),
    ).fetchall()
    # 一周内可能发过多次，必须求和，不能只看一行。
    used_map = {
        key: sum(int(row[key] or 0) for row in rows) for key in WELFARE_SINGLE_LIMITS
    }
    remaining: dict[str, int] = {}
    for key in WELFARE_SINGLE_LIMITS:
        requested = int(values.get(key, 0) or 0)
        used = used_map[key]
        left = int(quotas[key]) - used
        remaining[key] = max(0, left)
        if requested <= 0:
            continue
        if left < requested:
            raise ValueError(
                f"该成员本周 {WELFARE_LABELS[key]} 剩余额度 {max(0, left)}"
                f"（本周额度 {quotas[key]}，已用 {used}），不足以再发 {requested}。"
            )
    return remaining


def _restrict_private_file(path: Path) -> None:
    """Use owner-only POSIX permissions where the host filesystem supports them."""
    try:
        path.chmod(0o600)
    except OSError:
        pass




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
def hub_transaction(immediate: bool = False) -> Iterable[sqlite3.Connection]:
    conn = connect_hub()
    try:
        if immediate:
            conn.execute("BEGIN IMMEDIATE")
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
    for key, amount in {
        "cultivation": 100,
        "spirit_stone": 12,
        "spirit_wood": 4,
        "mystic_iron": 2,
        "star_sand": 2,
        "qi_grass": 3,
        "dew_flower": 2,
        "primordial_stone": 1,
    }.items():
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
        # 版本号可能被旧版直接写成 5 而没跑迁移；这里不看版本，缺列就补，作为兜底自愈。
        from core.migrations import repair_hub_welfare_columns

        repair_hub_welfare_columns(conn)
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
            invite = generate_invite_code()
            conn.execute(
                "INSERT INTO hub_invites(code,uses_remaining,expires_at,created_by,created_at) VALUES (?,?,?,?,?)",
                (invite, 9, (datetime.now().astimezone() + timedelta(days=30)).isoformat(timespec="seconds"), user_id, now_iso()),
            )

        conn.execute(
            "INSERT OR IGNORE INTO hub_settings(key,value) VALUES ('instance_id',?)",
            (generate_invite_code(8),),
        )


def get_user_by_token(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    return accounts.account_by_token(conn, token)


def get_hub_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM hub_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_hub_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO hub_settings(key,value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def hub_instance_id(conn: sqlite3.Connection) -> str:
    """Read-only per-database fingerprint; created at init or in write paths.

    Never INSERT from a read-only request: the admin page holds a WAL read
    snapshot, and upgrading it to a write fails with "database is locked".
    """
    row = conn.execute("SELECT value FROM hub_settings WHERE key='instance_id'").fetchone()
    return str(row["value"]) if row and row["value"] else ""


def ensure_instance_id(conn: sqlite3.Connection) -> str:
    """Create the fingerprint inside a context that already owns the write lock."""
    value = hub_instance_id(conn)
    if value:
        return value
    value = generate_invite_code(8)
    conn.execute("INSERT OR IGNORE INTO hub_settings(key,value) VALUES ('instance_id',?)", (value,))
    return hub_instance_id(conn) or value

