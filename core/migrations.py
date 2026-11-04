"""数据库迁移执行器。

解决一个问题：schema 演进时，老库必须能平滑升级，而不是"改一次表就重建一次库"。

三条硬承诺
----------
1. **版本化**：每个迁移是一个带版本号的对象，按顺序执行，执行结果记入
   `schema_migrations`，事后可追溯"这个库经历过什么"。
2. **快照先行**：任何迁移执行前，用 SQLite backup API 把整个库复制一份到
   `migration_snapshots/`。快照与普通文件复制不同，它能保证 WAL 模式下的
   一致性。保留最近 5 份。
3. **失败回滚**：迁移在事务内执行，任何一条语句失败都会回滚；`user_version`
   在全部成功后才提升，并在异常路径上显式复位一次——即使某些 SQLite 版本
   对 `user_version` 的写入不参与事务回滚，也不会出现"结构回退了、版本号
   却升上去了"的错位状态。

用法
----
给 `SCHEMA_VERSION` 加 1，并向前面的注册表追加一条迁移：

    from core.migrations import Migration

    PERSONAL_MIGRATIONS.append(
        Migration(
            version=3,
            note="为条目增加 lang 字段",
            statements=(
                "ALTER TABLE entries ADD COLUMN lang TEXT NOT NULL DEFAULT '';",
                "UPDATE entries SET lang='zh' WHERE lang='';",
            ),
        )
    )

不要修改已发布的 Migration；发现写错了，追加一条新版本去修正它。
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

KEEP_SNAPSHOTS = 5


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class Migration:
    """一次结构变更。

    statements 里的每条 SQL 都会用 `conn.execute` 逐条执行（而不是
    executescript——后者会在执行前隐式提交，破坏事务边界）。
    需要数据回填等 Python 逻辑时，用 runner；它与 statements 在同一个事务里。
    """

    version: int
    note: str
    statements: tuple[str, ...] = ()
    runner: Callable[[sqlite3.Connection], None] | None = None


# 已发布的迁移按版本号升序追加。基线版本由 BASE_SCHEMA_SQL 直接建出，
# 因此列表从"比当前 SCHEMA_VERSION 更早的下一个版本"开始写。
def _upgrade_experiment_ledger(conn: sqlite3.Connection) -> None:
    """Add the generic columns only when absent, then migrate wording/data.

    A legacy database may already carry a ``conditions`` column from an older
    experiment table, so the migration must be idempotent instead of trusting a
    bare ALTER statement.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(experiments)")}
    if "conditions" not in columns:
        conn.execute(
            "ALTER TABLE experiments ADD COLUMN conditions TEXT NOT NULL DEFAULT '';"
        )
    if "measurements" not in columns:
        conn.execute(
            "ALTER TABLE experiments ADD COLUMN measurements TEXT NOT NULL DEFAULT '';"
        )
    _genericize_experiment_ledger(conn)


def _genericize_experiment_ledger(conn: sqlite3.Connection) -> None:
    """Move factory EG wording and legacy values into the generic ledger.

    Only the exact factory workspace name is renamed, so a researcher who chose
    their own wording keeps it.  Legacy numeric columns stay untouched; their
    values are copied once into the free-text condition/measurement fields so
    old records remain readable after the UI stops showing domain fields.
    """
    conn.execute(
        "UPDATE workspaces SET name='通用DIY实验', "
        "description='任意方向的实验批次台账：条件、指标与结论自由填写' "
        "WHERE name IN ('EG 实验', 'EG实验')"
    )
    rows = conn.execute(
        "SELECT id, conditions, measurements, eg_content, water_cement_ratio, "
        "compaction_pressure, thickness_cm, area_cm2, electrolyte, voltage_min, "
        "voltage_max, scan_rate, specific_capacitance, conductivity, "
        "compressive_strength FROM experiments"
    ).fetchall()
    for row in rows:
        (
            row_id,
            conditions,
            measurements,
            eg_content,
            water_cement_ratio,
            compaction_pressure,
            thickness_cm,
            area_cm2,
            electrolyte,
            voltage_min,
            voltage_max,
            scan_rate,
            specific_capacitance,
            conductivity,
            compressive_strength,
        ) = tuple(row)
        if conditions or measurements:
            continue
        condition_parts: list[str] = []
        if eg_content is not None:
            condition_parts.append(f"EG 含量 {eg_content} wt%")
        if water_cement_ratio is not None:
            condition_parts.append(f"水胶比 {water_cement_ratio}")
        if compaction_pressure is not None:
            condition_parts.append(f"压实 {compaction_pressure} MPa")
        if thickness_cm is not None:
            condition_parts.append(f"厚度 {thickness_cm} cm")
        if area_cm2 is not None:
            condition_parts.append(f"面积 {area_cm2} cm²")
        if electrolyte:
            condition_parts.append(f"电解液 {electrolyte}")
        measurement_parts: list[str] = []
        if voltage_min is not None or voltage_max is not None:
            measurement_parts.append(f"电压窗 {voltage_min or 0}–{voltage_max or 0} V")
        if scan_rate is not None:
            measurement_parts.append(f"扫速 {scan_rate} mV/s")
        if specific_capacitance is not None:
            measurement_parts.append(f"比电容 {specific_capacitance} F/g")
        if conductivity is not None:
            measurement_parts.append(f"电导率 {conductivity} S/m")
        if compressive_strength is not None:
            measurement_parts.append(f"抗压强度 {compressive_strength} MPa")
        conn.execute(
            "UPDATE experiments SET conditions=?, measurements=? WHERE id=?",
            (
                ("旧版字段：" + "；".join(condition_parts)) if condition_parts else "",
                ("旧版字段：" + "；".join(measurement_parts)) if measurement_parts else "",
                row_id,
            ),
        )


PERSONAL_MIGRATIONS: list[Migration] = [
    # 1.2.0：趣味层默认打开。仅把仍是出厂默认 "off" 的库改为 "on"；
    # 用户若曾显式选择过其他值（理论上只有 on/off），保持尊重。
    Migration(
        version=3,
        note="趣味层默认打开",
        statements=(
            "UPDATE settings SET value='on' WHERE key='gamification' AND value='off';",
        ),
    ),    Migration(
        version=4,
        note="新增用户反馈表",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS user_feedback(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL DEFAULT 'bug',
                content TEXT NOT NULL,
                contact TEXT NOT NULL DEFAULT '',
                hub_synced INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            """,
        ),
    ),
    Migration(
        version=5,
        note="实验台账通用化：条件/指标字段与默认领域改名",
        runner=_upgrade_experiment_ledger,
    ),
    Migration(
        version=6,
        note="撤回机制：新增 upload_withdrawals 与 entry_revisions",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS upload_withdrawals(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id INTEGER NOT NULL,
                error_type TEXT NOT NULL,
                description TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending_review',
                auto_level TEXT NOT NULL DEFAULT 'auto',
                decided_by TEXT NOT NULL DEFAULT '',
                decision_note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                decided_at TEXT
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS entry_revisions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id INTEGER NOT NULL,
                rev_no INTEGER NOT NULL,
                snapshot_json TEXT NOT NULL,
                file_copy_path TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            """,
        ),
    ),
]
_HUB_LEGACY_IDENTITY_TABLES = (
    "hub_profiles",
    "hub_asset_transactions",
    "hub_inventory",
    "hub_sync_events",
    "hub_invites",
    "hub_releases",
    "hub_resource_cards",
    "hub_audit_log",
)


def _quoted_sql_name(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _repair_hub_account_foreign_keys(conn: sqlite3.Connection) -> None:
    """Retarget legacy identity foreign keys from ``hub_users`` to ``accounts``.

    Hub schema v3 moved identity data to ``accounts`` but some upgraded
    databases retained table definitions whose foreign keys still pointed at
    the retired ``hub_users`` table. Registration then created the account and
    credential successfully, but the first asset insert failed with a foreign
    key error and rolled the whole transaction back.

    SQLite cannot alter a foreign-key target in place, so affected tables are
    rebuilt with the same DDL except for the target table. ``CASCADE`` children
    without a matching account are intentionally omitted; ``SET NULL`` columns
    are retained with NULL. Indexes and triggers are recreated before the
    migration reports success.
    """
    create_table_re = re.compile(
        r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
        r"(?:\"[^\"]+\"|`[^`]+`|\[[^\]]+\]|[^\s(]+)",
        re.IGNORECASE,
    )
    legacy_reference_re = re.compile(
        r"\bREFERENCES\s+(?:\"hub_users\"|`hub_users`|\[hub_users\]|hub_users)\s*\(",
        re.IGNORECASE,
    )

    for table in _HUB_LEGACY_IDENTITY_TABLES:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue
        foreign_keys = [
            row
            for row in conn.execute(f"PRAGMA foreign_key_list({_quoted_sql_name(table)})")
            if str(row[2]) == "hub_users"
        ]
        if not foreign_keys:
            continue

        table_sql_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=? AND sql IS NOT NULL",
            (table,),
        ).fetchone()
        if not table_sql_row:
            raise RuntimeError(f"无法读取中心表 {table} 的建表语句。")
        table_sql = str(table_sql_row[0])
        temporary = f"__hub_v4_{table}"
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (temporary,)
        ).fetchone():
            raise RuntimeError(f"迁移临时表 {temporary} 已存在，已停止以避免覆盖数据。")

        rebuilt_sql, replacements = legacy_reference_re.subn(
            "REFERENCES accounts(", table_sql
        )
        if replacements != len({int(row[0]) for row in foreign_keys}):
            raise RuntimeError(f"中心表 {table} 的旧身份外键无法安全重写。")
        rebuilt_sql = create_table_re.sub(
            f"CREATE TABLE {_quoted_sql_name(temporary)}", rebuilt_sql, count=1
        )

        columns = [
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({_quoted_sql_name(table)})")
        ]
        if not columns:
            raise RuntimeError(f"无法读取中心表 {table} 的字段定义。")

        legacy_by_column = {str(row[3]): row for row in foreign_keys}
        selected_columns: list[str] = []
        cascade_filters: list[str] = []
        for column in columns:
            source = f"source.{_quoted_sql_name(column)}"
            foreign_key = legacy_by_column.get(column)
            if not foreign_key:
                selected_columns.append(source)
                continue
            on_delete = str(foreign_key[6]).upper()
            account_exists = (
                f"EXISTS (SELECT 1 FROM accounts WHERE accounts.id = {source})"
            )
            if on_delete == "CASCADE":
                cascade_filters.append(f"({source} IS NULL OR {account_exists})")
                selected_columns.append(source)
            elif on_delete == "SET NULL":
                selected_columns.append(
                    f"CASE WHEN {source} IS NULL OR {account_exists} "
                    f"THEN {source} ELSE NULL END AS {_quoted_sql_name(column)}"
                )
            else:
                raise RuntimeError(
                    f"中心表 {table}.{column} 使用不支持的旧外键动作 {on_delete}。"
                )

        dependent_objects = [
            str(row[0])
            for row in conn.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL "
                "ORDER BY CASE type WHEN 'index' THEN 0 ELSE 1 END, name",
                (table,),
            )
        ]

        conn.execute(rebuilt_sql)
        where_sql = f" WHERE {' AND '.join(cascade_filters)}" if cascade_filters else ""
        conn.execute(
            f"INSERT INTO {_quoted_sql_name(temporary)} "
            f"({','.join(_quoted_sql_name(column) for column in columns)}) "
            f"SELECT {', '.join(selected_columns)} FROM {_quoted_sql_name(table)} AS source"
            f"{where_sql}"
        )
        conn.execute(f"DROP TABLE {_quoted_sql_name(table)}")
        conn.execute(
            f"ALTER TABLE {_quoted_sql_name(temporary)} RENAME TO {_quoted_sql_name(table)}"
        )
        for object_sql in dependent_objects:
            conn.execute(object_sql)

    remaining = []
    for table in _HUB_LEGACY_IDENTITY_TABLES:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue
        if any(
            str(row[2]) == "hub_users"
            for row in conn.execute(f"PRAGMA foreign_key_list({_quoted_sql_name(table)})")
        ):
            remaining.append(table)
    if remaining:
        raise RuntimeError(f"中心表仍引用旧身份表：{', '.join(remaining)}")

    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        first = tuple(violations[0])
        raise RuntimeError(f"中心库迁移后仍有外键冲突：{first}")



def _upgrade_hub_welfare_v5(conn: sqlite3.Connection) -> None:
    """中心库 v4→v5：补齐福利表缺失列，并建立每周额度表。

    1.10.0 的建表语句早就写了 13 个福利列，但老库的 ``hub_welfare_grants``
    只有最初的 5 列；升级后管理员一发放就报 "no column named cultivation"，
    表现就是"福利发不出去"。这里按实际列名增量补，重复执行安全。
    """
    existing = {str(row["name"]) for row in conn.execute("PRAGMA table_info(hub_welfare_grants)")}
    for name in (
        "cultivation",
        "qi_grass",
        "dew_flower",
        "red_ganoderma",
        "azure_sword",
        "heart_mirror",
        "cosmos_bag",
        "primordial_stone",
    ):
        if name not in existing:
            conn.execute(
                f"ALTER TABLE hub_welfare_grants ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0"
            )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS hub_welfare_week_quota(
            week_key TEXT PRIMARY KEY,
            quotas_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )


HUB_MIGRATIONS: list[Migration] = [
    Migration(
        version=4,
        note="修复中心库旧身份外键：hub_users → accounts",
        runner=_repair_hub_account_foreign_keys,
    ),
    Migration(
        version=5,
        note="福利表补齐 8 种材料列，并建立每周额度表",
        runner=_upgrade_hub_welfare_v5,
    ),
]


def _validate(migrations: Iterable[Migration], target_version: int) -> list[Migration]:
    ordered = sorted(migrations, key=lambda m: m.version)
    seen: set[int] = set()
    for migration in ordered:
        if migration.version in seen:
            raise ValueError(f"迁移版本 {migration.version} 重复定义。")
        seen.add(migration.version)
        if not migration.statements and migration.runner is None:
            raise ValueError(f"迁移 {migration.version} 既没有 statements 也没有 runner。")
        if migration.version > target_version:
            raise ValueError(f"迁移 {migration.version} 超过了目标版本 {target_version}。")
    return ordered


def snapshot_database(
    db_path: Path,
    snapshot_dir: Path,
    *,
    label: str,
    target_version: int,
    keep: int = KEEP_SNAPSHOTS,
) -> Path:
    """迁移前快照：用 backup API 保证 WAL 模式下的一致性。"""
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = snapshot_dir / f"{label}_to_v{target_version}_{stamp}.db"
    source = sqlite3.connect(db_path)
    copy = sqlite3.connect(target)
    try:
        source.backup(copy)
    finally:
        copy.close()
        source.close()
    old = sorted(snapshot_dir.glob(f"{label}_to_v*.db"), key=lambda p: p.stat().st_mtime)
    for stale in old[:-keep] if keep else []:
        stale.unlink(missing_ok=True)
    return target


def pending(migrations: list[Migration], current_version: int) -> list[Migration]:
    return [m for m in sorted(migrations, key=lambda m: m.version) if m.version > current_version]


def apply_migrations(
    conn: sqlite3.Connection,
    *,
    label: str,
    db_path: Path,
    snapshot_dir: Path,
    migrations: list[Migration],
    current_version: int,
    target_version: int,
) -> list[int]:
    """把 current_version 之前的库升级到 target_version，返回实际执行的版本号。

    必须在调用方的事务里运行：任何失败都由调用方回滚，
    本函数只负责在异常路径上把 `user_version` 显式复位。
    """
    ordered = _validate(migrations, target_version)
    due = [m for m in ordered if m.version > current_version]
    if not due:
        return []

    snapshot_database(db_path, snapshot_dir, label=label, target_version=target_version)

    applied: list[int] = []
    # sqlite3 驱动只在 DML 前隐式开启事务，DDL（ALTER/CREATE）会立即提交。
    # 迁移必须整体原子，所以这里显式管理事务边界。
    own_transaction = not conn.in_transaction
    if own_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        for migration in due:
            for statement in migration.statements:
                conn.execute(statement)
            if migration.runner is not None:
                migration.runner(conn)
            conn.execute(
                "INSERT OR REPLACE INTO schema_migrations(version,applied_at,note) VALUES (?,?,?)",
                (migration.version, now_iso(), migration.note),
            )
            applied.append(migration.version)
        conn.execute(f"PRAGMA user_version = {target_version}")
        if own_transaction:
            conn.execute("COMMIT")
    except Exception:
        if own_transaction and conn.in_transaction:
            conn.execute("ROLLBACK")
        # user_version 写在库头。多数 SQLite 版本会随事务回滚；
        # 这里再显式复位一次，防御"结构回退了、版本号却升上去"的错位。
        conn.execute(f"PRAGMA user_version = {current_version}")
        raise
    return applied


def ensure_base_record(conn: sqlite3.Connection, version: int, note: str = "initial schema") -> None:
    """给全新建库补一条基线记录；1.0.0 时代建出的 v2 库也会在这里补齐。"""
    exists = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version=?", (version,)
    ).fetchone()
    if not exists:
        conn.execute(
            "INSERT INTO schema_migrations(version,applied_at,note) VALUES (?,?,?)",
            (version, now_iso(), note),
        )
