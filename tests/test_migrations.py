"""迁移执行器契约测试。

全部走子进程：`runtime_paths` 在导入时读取环境变量，进程内改不了数据目录。
每个脚本自带断言，失败即非零退出。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(script: str) -> str:
    environment = dict(os.environ)
    environment["RESEARCH_OS_DATA_DIR"] = tempfile.mkdtemp(prefix="wendao-migration-test-")
    environment["PYTHONPATH"] = str(ROOT)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"migration script failed:\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


UPGRADE = """
from core.database import DB_PATH, connect, init_db
from core.migrations import Migration
import core.database as dbm

# 1) 建 v2 库，写入一条可被迁移改写的数据
init_db()
with connect() as connection:
    connection.execute(
        "INSERT INTO activities(action,xp,detail,created_at) VALUES ('mtest',1,'迁移前','2026-01-01T00:00:00+08:00')"
    )
    connection.commit()

# 2) 注册一条 v3 迁移：加列 + 回填
dbm.PERSONAL_MIGRATIONS.append(
    Migration(
        version=5,
        note="为 activities 增加 note 字段并回填",
        statements=(
            "ALTER TABLE activities ADD COLUMN note TEXT NOT NULL DEFAULT '';",
            "UPDATE activities SET note='migrated' WHERE note='';",
        ),
    )
)
dbm.SCHEMA_VERSION = 5

# 3) 升级
init_db()
with connect() as connection:
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
    columns = {row[1] for row in connection.execute("PRAGMA table_info(activities)")}
    assert "note" in columns
    row = connection.execute("SELECT * FROM activities WHERE action='mtest'").fetchone()
    assert row["note"] == "migrated", "已有数据必须被回填"
    versions = [r[0] for r in connection.execute("SELECT version FROM schema_migrations ORDER BY version")]
    assert versions == [4, 5], versions

snapshots = list((DB_PATH.parent / "migration_snapshots").glob("wendao_to_v5_*.db"))
assert len(snapshots) == 1, snapshots
assert snapshots[0].stat().st_size > 0
print("upgrade-ok")
"""

FAILURE = """
from core.database import DB_PATH, connect, init_db
from core.migrations import Migration
import core.database as dbm

init_db()
with connect() as connection:
    connection.execute(
        "INSERT INTO activities(action,xp,detail,created_at) VALUES ('keep',1,'保留','2026-01-01T00:00:00+08:00')"
    )
    connection.commit()

dbm.PERSONAL_MIGRATIONS.append(
    Migration(
        version=5,
        note="第二条会失败",
        statements=(
            "ALTER TABLE activities ADD COLUMN note TEXT NOT NULL DEFAULT '';",
            "UPDATE activities SET no_such_column=1;",
        ),
    )
)
dbm.SCHEMA_VERSION = 5
try:
    init_db()
except RuntimeError:
    pass
else:
    raise SystemExit("坏迁移必须抛 RuntimeError")

with connect() as connection:
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 4, "失败后版本号必须留在原位"
    columns = {row[1] for row in connection.execute("PRAGMA table_info(activities)")}
    assert "note" not in columns, "失败迁移的结构变更必须回滚"
    assert connection.execute("SELECT COUNT(*) n FROM activities").fetchone()["n"] == 1
    ledger = [r[0] for r in connection.execute("SELECT version FROM schema_migrations ORDER BY version")]
    assert ledger == [4], ledger

snapshots = list((DB_PATH.parent / "migration_snapshots").glob("wendao_to_v5_*.db"))
assert len(snapshots) == 1, "失败也要留下迁移前快照"
print("rollback-ok")
"""

NEWER = """
from core.database import connect, init_db
init_db()
with connect() as connection:
    connection.execute("PRAGMA user_version = 99")
    connection.commit()
try:
    init_db()
except RuntimeError as exc:
    assert "更新" in str(exc)
    print("newer-rejected")
else:
    raise SystemExit("更新版本的库必须被拒绝")
"""

FOREIGN = """
import sqlite3
from core.runtime_paths import INSTANCE_DIR
from core.database import init_db
INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(INSTANCE_DIR / "wendao.db") as connection:
    connection.execute("CREATE TABLE other_tool(id INTEGER PRIMARY KEY)")
    connection.commit()
try:
    init_db()
except RuntimeError as exc:
    assert "schema_migrations" in str(exc)
    print("foreign-rejected")
else:
    raise SystemExit("外来数据库必须被拒绝")
"""

HUB = """
from core.migrations import Migration
from hub import db as dbm
from hub.db import connect_hub, init_hub_db

init_hub_db()
dbm.HUB_MIGRATIONS.append(
    Migration(
        version=4,
        note="为账号增加 locale 字段",
        statements=(
            "ALTER TABLE accounts ADD COLUMN locale TEXT NOT NULL DEFAULT 'zh-CN';",
        ),
    )
)
dbm.HUB_SCHEMA_VERSION = 4
init_hub_db()
with connect_hub() as connection:
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
    columns = {row[1] for row in connection.execute("PRAGMA table_info(accounts)")}
    assert "locale" in columns
snapshots = list((dbm.INSTANCE_DIR / "migration_snapshots").glob("hub_to_v4_*.db"))
assert len(snapshots) == 1, snapshots
print("hub-upgrade-ok")
"""


class MigrationExecutorTests(unittest.TestCase):
    def test_upgrade_preserves_data_and_takes_snapshot(self) -> None:
        self.assertIn("upgrade-ok", _run(UPGRADE))

    def test_failed_migration_rolls_back_schema_and_version(self) -> None:
        self.assertIn("rollback-ok", _run(FAILURE))

    def test_newer_database_is_rejected(self) -> None:
        self.assertIn("newer-rejected", _run(NEWER))

    def test_foreign_database_is_rejected(self) -> None:
        self.assertIn("foreign-rejected", _run(FOREIGN))

    def test_hub_database_can_be_migrated(self) -> None:
        self.assertIn("hub-upgrade-ok", _run(HUB))


if __name__ == "__main__":
    unittest.main()
