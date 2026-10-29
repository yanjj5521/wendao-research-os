from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from core import migrations as migration_engine
from core.bootstrap import seed_clean_database
from core.migrations import PERSONAL_MIGRATIONS
from core.navigation import DEFAULT_NAV_LABELS, normalize_nav_layout
from core.schema import (
    BASE_SCHEMA_SQL,
    DATABASE_FILENAME,
    FTS_SCHEMA_SQL,
    SCHEMA_VERSION,
)
from core.runtime_paths import INSTANCE_DIR


DB_PATH = INSTANCE_DIR / DATABASE_FILENAME

# Bumping this marker re-applies the learning-first wording once.  Only values
# that still equal the previous shipped default are touched, so a researcher who
# renamed the navigation or rewrote the compass keeps their own words.
POSITIONING_COPY_VERSION = "learning-first-1"

LEGACY_NAV_LABELS = {
    "daily": "每日任务",
    "cultivation": "修炼记录",
    "projects": "课题推进",
    "career": "生涯罗盘",
    "literature_report": "文献汇报",
    "workbench": "实用工作台",
    "workspaces": "工作区管理",
    "group_cultivation": "科研主线",
    "group_knowledge": "轻量推进",
    "group_workspaces": "我的工作区",
    "group_growth": "成长与趣味",
}

LEGACY_HOME_MOTTO = "让科研更好玩一点"

LEGACY_CAREER_COPY = {
    "career_focus": "把当前学习转化为可复用、可验证的科研能力。",
    "career_boundary": "不为尚未确定的长期主线提前承诺，只推进当前证据最需要的一步。",
    "career_success_signal": "能独立解释、复现或用证据修正一个关键判断。",
    "career_focus_new": "把每天的学习变成能带走的能力，而不是一次性看完的内容。",
    "career_boundary_new": "不要求把每个方向都学完；只保证当前这一层能复述、能用、能迁移。",
    "career_success_signal_new": "能不看资料讲清一个概念，并在新问题上用出来。",
}


def _read_setting(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return str(row["value"]) if row else None


def _write_setting(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        """
        INSERT INTO settings(key,value) VALUES (?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (key, value),
    )


def refresh_positioning_copy(connection: sqlite3.Connection) -> None:
    """Adopt the learning-first wording without overwriting personal edits.

    Projects used to sit in the default navigation; now they live in their own
    "研究与课题" group and stay opt-in.  Re-normalising the saved layout moves
    them there, drops unknown keys, and appends the new group for old installs.
    """
    marker = _read_setting(connection, "ui_copy_version") or ""
    if marker == POSITIONING_COPY_VERSION:
        return

    raw_labels = _read_setting(connection, "nav_labels")
    if raw_labels:
        try:
            stored = json.loads(raw_labels)
        except json.JSONDecodeError:
            stored = {}
        if isinstance(stored, dict):
            refreshed = dict(DEFAULT_NAV_LABELS)
            for key, legacy in LEGACY_NAV_LABELS.items():
                candidate = str(stored.get(key, "")).strip()
                current = refreshed.get(key, "")
                refreshed[key] = current if not candidate or candidate == legacy else candidate[:24]
            for key, value in stored.items():
                if key in refreshed or key.startswith("group_"):
                    continue
                text = str(value).strip()
                if text:
                    refreshed[key] = text[:24]
            _write_setting(connection, "nav_labels", json.dumps(refreshed, ensure_ascii=False))

    raw_layout = _read_setting(connection, "nav_layout")
    if raw_layout:
        try:
            parsed = json.loads(raw_layout)
        except json.JSONDecodeError:
            parsed = []
        _write_setting(
            connection,
            "nav_layout",
            json.dumps(normalize_nav_layout(parsed), ensure_ascii=False),
        )

    motto = _read_setting(connection, "ui_home_motto")
    if motto == LEGACY_HOME_MOTTO:
        _write_setting(connection, "ui_home_motto", "把学习变成可以积累的事")

    for old_key, new_key in (
        ("career_focus", "career_focus_new"),
        ("career_boundary", "career_boundary_new"),
        ("career_success_signal", "career_success_signal_new"),
    ):
        if _read_setting(connection, old_key) == LEGACY_CAREER_COPY[old_key]:
            _write_setting(connection, old_key, LEGACY_CAREER_COPY[new_key])

    _write_setting(connection, "ui_copy_version", POSITIONING_COPY_VERSION)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


@contextmanager
def transaction() -> Iterable[sqlite3.Connection]:
    connection = connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    with transaction() as connection:
        table_count = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                """
            ).fetchone()[0]
        )
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if table_count:
            has_ledger = bool(
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
                ).fetchone()
            )
            if not has_ledger:
                raise RuntimeError(
                    "instance 下存在不是本程序创建的数据库（缺少 schema_migrations）。"
                    "请把它移走或改名，让程序重新建库；不要把别的库混进来。"
                )
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"数据库结构版本 v{version} 比当前程序（v{SCHEMA_VERSION}）更新，"
                    "请升级程序后再打开，避免把新结构当旧结构改坏。"
                )
            if version < SCHEMA_VERSION:
                snapshot_dir = INSTANCE_DIR / "migration_snapshots"
                try:
                    migration_engine.apply_migrations(
                        connection,
                        label="wendao",
                        db_path=DB_PATH,
                        snapshot_dir=snapshot_dir,
                        migrations=PERSONAL_MIGRATIONS,
                        current_version=version,
                        target_version=SCHEMA_VERSION,
                    )
                except Exception as exc:
                    raise RuntimeError(
                        f"数据库升级失败，已回滚到 v{version}；迁移前快照在 {snapshot_dir}。"
                        f"解决前请勿继续使用本程序。原因：{exc}"
                    ) from exc
            refresh_positioning_copy(connection)
            migration_engine.ensure_base_record(connection, SCHEMA_VERSION)
            return

        connection.executescript(BASE_SCHEMA_SQL)
        try:
            connection.executescript(FTS_SCHEMA_SQL)
        except sqlite3.OperationalError:
            pass
        seed_clean_database(connection, now_iso)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        migration_engine.ensure_base_record(connection, SCHEMA_VERSION)


def get_setting(key: str, default: str = "") -> str:
    with connect() as connection:
        row = connection.execute(
            "SELECT value FROM settings WHERE key=?",
            (key,),
        ).fetchone()
        return str(row["value"]) if row else default


def set_setting(key: str, value: str) -> None:
    with transaction() as connection:
        connection.execute(
            """
            INSERT INTO settings(key,value) VALUES (?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, value),
        )


def log_activity(
    action: str,
    xp: int,
    detail: str = "",
    entry_id: int | None = None,
) -> None:
    with transaction() as connection:
        connection.execute(
            """
            INSERT INTO activities(action,entry_id,xp,detail,created_at)
            VALUES (?,?,?,?,?)
            """,
            (action, entry_id, xp, detail, now_iso()),
        )


def total_xp(connection: sqlite3.Connection | None = None) -> int:
    owns_connection = connection is None
    connection = connection or connect()
    try:
        row = connection.execute(
            "SELECT COALESCE(SUM(xp),0) AS xp FROM activities"
        ).fetchone()
        return int(row["xp"])
    finally:
        if owns_connection:
            connection.close()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None
