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
]
HUB_MIGRATIONS: list[Migration] = []


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
