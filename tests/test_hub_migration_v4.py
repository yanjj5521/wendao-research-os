"""Hub schema v4: repair legacy hub_users foreign keys without losing accounts."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LEGACY_HUB = r'''
import sqlite3
from datetime import datetime, timedelta

from core import accounts
from core.runtime_paths import INSTANCE_DIR
from core.schema import HUB_SCHEMA_SQL
import hub.db as dbm

INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
db_path = INSTANCE_DIR / "hub.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
conn.executescript(HUB_SCHEMA_SQL)

legacy_tables = (
    "hub_asset_transactions", "hub_inventory", "hub_sync_events", "hub_invites",
    "hub_releases", "hub_resource_cards", "hub_audit_log",
)
for table in legacy_tables:
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()[0]
    conn.execute(f'DROP TABLE "{table}"')
    conn.execute(sql.replace("REFERENCES accounts(", "REFERENCES hub_users("))

conn.execute("""CREATE TABLE hub_profiles(
    user_id INTEGER PRIMARY KEY,
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
    FOREIGN KEY(user_id) REFERENCES hub_users(id) ON DELETE CASCADE
)""")
conn.execute("CREATE TABLE hub_users(id INTEGER PRIMARY KEY)")
conn.execute("CREATE INDEX idx_hub_events_user_created ON hub_sync_events(user_id,created_at DESC)")
conn.execute("CREATE INDEX idx_hub_assets_user_created ON hub_asset_transactions(user_id,created_at DESC)")
conn.execute("CREATE INDEX idx_hub_releases_created ON hub_releases(created_at DESC)")

account_id, _ = accounts.create_account(
    conn, "legacyadmin", "Passw0rd!", "旧管理员", role="admin", email=None
)
assert account_id == 1
conn.execute("INSERT INTO hub_users(id) VALUES (?)", (account_id,))
ts = datetime.now().astimezone().isoformat(timespec="seconds")
future = (datetime.now().astimezone() + timedelta(days=3)).isoformat(timespec="seconds")

for user_id in (account_id, 999):
    conn.execute(
        "INSERT INTO hub_asset_transactions(user_id,asset_key,amount,reason,created_at) VALUES (?,?,?,?,?)",
        (user_id, "cultivation", 1, f"v4-valid-{user_id}", ts),
    )
    conn.execute(
        "INSERT INTO hub_inventory(user_id,item_key,item_type,quantity,level,equipped,updated_at) VALUES (?,?,?,?,?,?,?)",
        (user_id, f"item-{user_id}", "test", 1, 1, 0, ts),
    )
    conn.execute(
        "INSERT INTO hub_sync_events(user_id,event_uuid,event_type,payload_json,rewarded,result_json,created_at) VALUES (?,?,?,?,?,?,?)",
        (user_id, f"event-{user_id}", "test", "{}", 0, "{}", ts),
    )
    conn.execute(
        "INSERT INTO hub_invites(code,uses_remaining,expires_at,created_by,created_at) VALUES (?,?,?,?,?)",
        (f"INVITE{user_id}", 2, future, user_id, ts),
    )
    conn.execute(
        "INSERT INTO hub_releases(version,title,notes,file_path,file_size,sha256,active,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (f"v-{user_id}", "迁移测试", "", "missing.zip", 0, "hash", 0, user_id, ts),
    )
    conn.execute(
        "INSERT INTO hub_resource_cards(user_id,title,summary,tags,source_url,visibility,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (user_id, f"card-{user_id}", "", "", "", "team", ts, ts),
    )
    conn.execute(
        "INSERT INTO hub_audit_log(user_id,action,detail,ip,created_at) VALUES (?,?,?,?,?)",
        (user_id, "legacy", f"audit-{user_id}", "", ts),
    )
    conn.execute(
        "INSERT INTO hub_profiles(user_id,title,bio,skills,capabilities,goals,avatar_symbol,theme_json,public,revision,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (user_id, f"title-{user_id}", "", "", "", "", "道", "{}", 1, 1, ts),
    )

conn.execute("INSERT INTO schema_migrations(version,applied_at,note) VALUES (3,?, 'legacy v3')", (ts,))
conn.execute("PRAGMA user_version = 3")
conn.commit()
conn.close()

dbm.init_hub_db()
with dbm.connect_hub() as conn:
    assert conn.execute("PRAGMA user_version").fetchone()[0] == dbm.HUB_SCHEMA_VERSION
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert accounts.authenticate(conn, "legacyadmin", "Passw0rd!") is not None
    for table in (*legacy_tables, "hub_profiles"):
        assert not any(
            fk[2] == "hub_users"
            for fk in conn.execute(f'PRAGMA foreign_key_list("{table}")')
        ), table

    # CASCADE children without an account are removed.
    assert conn.execute(
        "SELECT COUNT(*) FROM hub_asset_transactions WHERE reason LIKE 'v4-valid-999'"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM hub_inventory WHERE user_id=999"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM hub_sync_events WHERE user_id=999"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM hub_resource_cards WHERE user_id=999"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM hub_profiles WHERE user_id=999"
    ).fetchone()[0] == 0

    # SET NULL rows survive, while their orphan identity becomes NULL.
    assert conn.execute(
        "SELECT created_by FROM hub_invites WHERE code='INVITE999'"
    ).fetchone()[0] is None
    assert conn.execute(
        "SELECT created_by FROM hub_releases WHERE version='v-999'"
    ).fetchone()[0] is None
    assert conn.execute(
        "SELECT user_id FROM hub_audit_log WHERE detail='audit-999'"
    ).fetchone()[0] is None
    assert conn.execute("SELECT COUNT(*) FROM hub_invites").fetchone()[0] == 2

    indexes = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_hub_%'"
        )
    }
    assert {"idx_hub_events_user_created", "idx_hub_assets_user_created", "idx_hub_releases_created"} <= indexes

    invite = "BLANKMAIL1"
    conn.execute(
        "INSERT INTO hub_invites(code,uses_remaining,expires_at,created_by,created_at) VALUES (?,?,?,?,?)",
        (invite, 1, future, account_id, ts),
    )
    member_id, _ = dbm.create_user(conn, "blankmail", "Passw0rd!", "空邮箱成员", email=None)
    assert member_id > account_id
    assert conn.execute(
        "SELECT email FROM accounts WHERE id=?", (member_id,)
    ).fetchone()[0] is None
    assert conn.execute(
        "UPDATE hub_invites SET uses_remaining=uses_remaining-1 WHERE code=? AND uses_remaining>0",
        (invite,),
    ).rowcount == 1
    conn.commit()

snapshots = list((INSTANCE_DIR / "migration_snapshots").glob("hub_to_v*_*.db"))
assert len(snapshots) == 1, snapshots
print("hub-migration-v4-ok")
'''


class HubMigrationV4Tests(unittest.TestCase):
    def test_legacy_hub_users_foreign_keys_are_repaired(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hub-v4-migration-") as directory:
            env = os.environ.copy()
            env["RESEARCH_OS_DATA_DIR"] = directory
            env["PYTHONPATH"] = str(ROOT)
            result = subprocess.run(
                [sys.executable, "-c", LEGACY_HUB],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=180,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-5000:])
            self.assertIn("hub-migration-v4-ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
