from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class HubWelfareTests(unittest.TestCase):
    """管理员发福利：老库升级补列、累计额度、按境界与人数缩放。"""

    def test_welfare_upgrade_and_scaling(self) -> None:
        script = r'''
import json
import re
import sqlite3
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from core.migrations import repair_hub_welfare_columns
from hub.app import app
from hub.db import (
    HUB_ADMIN_PATH,
    connect_hub,
    create_user,
    hub_transaction,
    init_hub_db,
    weekly_quota_key,
    weekly_quota_meta,
    weekly_quotas,
    welfare_scale,
)

# --- 0) 老库升级：只有 5 列时必须补齐 8 列，否则发放会报 no column named ... ---
legacy = sqlite3.connect(":memory:")
legacy.row_factory = sqlite3.Row
legacy.execute(
    """CREATE TABLE hub_welfare_grants(
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
        claimed_at TEXT
    )"""
)
repair_hub_welfare_columns(legacy)
cols = {row["name"] for row in legacy.execute("PRAGMA table_info(hub_welfare_grants)")}
for name in ("cultivation", "qi_grass", "dew_flower", "red_ganoderma",
             "azure_sword", "heart_mirror", "cosmos_bag", "primordial_stone"):
    assert name in cols, name
repair_hub_welfare_columns(legacy)  # 幂等
assert legacy.execute(
    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='hub_welfare_week_quota'"
).fetchone()
legacy.close()

# --- 0.5) 版本号被写成 5、但福利列从未补（1.10–1.12 的真实故障形态）---
# 直接改当前库：删表重建为 11 列并伪造 user_version=5，再走一次 init_hub_db。
import sqlite3
from hub.db import HUB_DB_PATH

init_hub_db()
legacy_conn = sqlite3.connect(HUB_DB_PATH)
legacy_conn.executescript(
    """
    DROP TABLE hub_welfare_grants;
    CREATE TABLE hub_welfare_grants(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      recipient_id INTEGER NOT NULL, granted_by INTEGER NOT NULL,
      xp INTEGER NOT NULL DEFAULT 0, spirit_stone INTEGER NOT NULL DEFAULT 0,
      spirit_wood INTEGER NOT NULL DEFAULT 0, mystic_iron INTEGER NOT NULL DEFAULT 0,
      star_sand INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL, claimed_at TEXT
    );
    PRAGMA user_version = 5;
    """
)
legacy_conn.commit()
legacy_conn.close()
init_hub_db()  # 启动自愈 + v6 迁移
check = sqlite3.connect(HUB_DB_PATH)
cols = {row[1] for row in check.execute("PRAGMA table_info(hub_welfare_grants)")}
version = check.execute("PRAGMA user_version").fetchone()[0]
check.close()
for name in ("cultivation", "qi_grass", "dew_flower", "red_ganoderma",
             "azure_sword", "heart_mirror", "cosmos_bag", "primordial_stone"):
    assert name in cols, f"启动后仍缺列 {name}"
assert int(version) >= 6, version

# --- 1) 真正走一遍管理员发福利 ---
init_hub_db()
with hub_transaction() as conn:
    member_id, _ = create_user(conn, "welfaremember", "Passw0rd!123", "受惠成员")
admin_password = re.search(r"密码: (.+)", HUB_ADMIN_PATH.read_text(encoding="utf-8")).group(1).strip()

client = TestClient(app)
page = client.get("/")
csrf = re.search(r'name="_csrf" value="([^"]+)"', page.text).group(1)
login = client.post(
    "/login",
    data={"username": "admin", "password": admin_password, "_csrf": csrf},
    follow_redirects=False,
)
assert login.status_code == 303, login.status_code
admin_page = client.get("/admin")
assert admin_page.status_code == 200
csrf = re.search(r'name="_csrf" value="([^"]+)"', admin_page.text).group(1)

grant = client.post(
    "/admin/welfare",
    data={"_csrf": csrf, "recipient_id": member_id, "xp": 10, "cultivation": 5, "reason": "回归测试"},
    follow_redirects=False,
)
assert grant.status_code == 303, grant.status_code
with connect_hub() as conn:
    row = conn.execute(
        "SELECT xp, cultivation FROM hub_welfare_grants WHERE recipient_id=? ORDER BY id DESC LIMIT 1",
        (member_id,),
    ).fetchone()
assert int(row["xp"]) == 10 and int(row["cultivation"]) == 5, dict(row)

# --- 2) 累计额度：一周内多次发放必须相加 ---
key = weekly_quota_key()
with hub_transaction() as conn:
    conn.execute(
        "INSERT INTO hub_welfare_week_quota(week_key,quotas_json,created_at) VALUES (?,?,?)"
        " ON CONFLICT(week_key) DO UPDATE SET quotas_json=excluded.quotas_json",
        (key, json.dumps({"quotas": {"xp": 16, "cultivation": 100}}), "2026-01-01T00:00:00+00:00"),
    )
second = client.post(
    "/admin/welfare",
    data={"_csrf": csrf, "recipient_id": member_id, "xp": 5},
    follow_redirects=False,
)
assert second.status_code == 303
over = client.post(
    "/admin/welfare",
    data={"_csrf": csrf, "recipient_id": member_id, "xp": 5},
    follow_redirects=False,
)
assert over.status_code == 303
with connect_hub() as conn:
    used = int(
        conn.execute(
            "SELECT COALESCE(SUM(xp),0) n FROM hub_welfare_grants WHERE recipient_id=?",
            (member_id,),
        ).fetchone()["n"]
    )
assert used == 15, used  # 10 + 5 允许（上限 16），第三次 5 因只剩 1 被拒
with connect_hub() as conn:
    conn.execute("DELETE FROM hub_welfare_week_quota WHERE week_key=?", (key,))
    conn.commit()

# --- 3) 境界与人数缩放：乘数进入 meta，额度随之上调 ---
with hub_transaction() as conn:
    admin_id = int(conn.execute("SELECT id FROM accounts WHERE role='admin' LIMIT 1").fetchone()["id"])
    conn.execute(
        "INSERT INTO hub_asset_transactions(user_id,asset_key,amount,reason,created_at) VALUES (?,?,?,?,?)",
        (admin_id, "cultivation", 5000, "测试修为", "2026-01-01T00:00:00+00:00"),
    )
    for index in range(6):
        create_user(conn, f"scalemember{index}", "Passw0rd!123", f"缩放成员{index}")
scale = welfare_scale()
assert scale["realm_name"] == "化神", scale
assert float(scale["realm_bonus"]) == 1.55
assert int(scale["members"]) >= 7
assert float(scale["member_bonus"]) > 1.0
assert float(scale["total_bonus"]) > 1.5

base = weekly_quotas()
meta = weekly_quota_meta()
assert meta.get("realm_name") == "化神", meta
assert int(base["xp"]) >= 600, base  # 200*3 下限 × 1.55+ 的加成

# 跨周重新生成，乘数变化后额度也要变
future = datetime.now().astimezone() + timedelta(days=8)
future_quota = weekly_quotas(now=future)
assert weekly_quota_key(future) != key
assert int(future_quota["spirit_stone"]) >= 150 * 3
print("welfare pass")
'''
        with tempfile.TemporaryDirectory(prefix="welfare-") as directory:
            env = os.environ.copy()
            env["RESEARCH_OS_DATA_DIR"] = directory
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("welfare pass", result.stdout)


if __name__ == "__main__":
    unittest.main()
