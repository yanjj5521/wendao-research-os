from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WelfareWeeklyAndRedPacketTests(unittest.TestCase):
    """福利额度周刷新（随机+下限）、全类型发放、红包扩展与全员通知。"""

    def test_all_scenarios(self) -> None:
        script = r'''
import re
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from hub.app import app as hub_app
from hub.db import (
    welfare_scale,
    WELFARE_SINGLE_LIMITS, WELFARE_WEEKLY_FLOORS, WELFARE_WEEKLY_CAPS,
    balances, connect_hub, init_hub_db, weekly_quotas, transact_asset,
)
from core import accounts as accounts_repo

init_hub_db()

# ── 建号：管理员 + 两名成员 ──
with connect_hub() as conn:
    accounts_repo.create_account(
        conn, username="admin2", password="Passw0rd!", display_name="管理二", role="admin",
    )
    accounts_repo.create_account(
        conn, username="alpha", password="Passw0rd!", display_name="成员甲", role="member",
    )
    accounts_repo.create_account(
        conn, username="beta", password="Passw0rd!", display_name="成员乙", role="member",
    )

def csrf(client, path="/login"):
    return re.search(r'name="_csrf" value="([^"]+)"', client.get(path).text).group(1)

admin = TestClient(hub_app, base_url="http://hubserver", root_path="")
admin.post("/login", data={"username": "admin2", "password": "Passw0rd!", "_csrf": csrf(admin)})
alpha = TestClient(hub_app, base_url="http://hubserver", root_path="")
alpha.post("/login", data={"username": "alpha", "password": "Passw0rd!", "_csrf": csrf(alpha)})
beta = TestClient(hub_app, base_url="http://hubserver", root_path="")
beta.post("/login", data={"username": "beta", "password": "Passw0rd!", "_csrf": csrf(beta)})

with connect_hub() as conn:
    admin_id = conn.execute("SELECT id FROM accounts WHERE username='admin2'").fetchone()[0]
    alpha_id = conn.execute("SELECT id FROM accounts WHERE username='alpha'").fetchone()[0]
    beta_id = conn.execute("SELECT id FROM accounts WHERE username='beta'").fetchone()[0]

# ── 1) 每周额度：随机但落在 [floor, cap]，周内稳定 ──
with connect_hub() as conn:
    q1 = weekly_quotas(conn)
scaled_cap = WELFARE_WEEKLY_CAPS
scale = welfare_scale()
factor = float(scale["total_bonus"])
assert all(WELFARE_WEEKLY_FLOORS[k] <= q1[k] <= int(WELFARE_WEEKLY_CAPS[k] * factor) + 1 for k in q1)
with connect_hub() as conn:
    assert weekly_quotas(conn) == q1, "同一周内额度必须稳定，不能反复刷新"

# ── 2) 管理页显示全类型 + 本周额度 ──
page = admin.get("/admin").text
assert "welfare_labels" not in page  # 模板渲染后不残留变量名
for key, label in (("qi_grass", "青灵草"), ("dew_flower", "凝露花"), ("primordial_stone", "混元石")):
    assert f'name="{key}"' in page, f"福利表单缺少 {key}"
    assert label in page
assert "额度为每人每周上限" in page and "计算口径" in page

# ── 3) 全类型发放：新列真实入账 ──
r = admin.post("/admin/welfare", data={
    "recipient_id": alpha_id, "xp": 100, "spirit_stone": 50, "qi_grass": 10,
    "dew_flower": 5, "red_ganoderma": 2, "azure_sword": 1, "heart_mirror": 1,
    "cosmos_bag": 1, "mystic_iron": 5, "star_sand": 5, "primordial_stone": 1,
    "reason": "全类型覆盖测试", "_csrf": csrf(admin, "/admin"),
})
assert "福利已入账" in r.text, r.text[:300]
with connect_hub() as conn:
    bal = balances(conn, alpha_id)
    assert bal["qi_grass"] >= 10 and bal["red_ganoderma"] >= 2 and bal["primordial_stone"] >= 1, bal

# ── 4) 周额度校验：把本周 xp 配额调小后超额请求 → 拒绝并给出剩余 ──
import json as _json
with connect_hub() as conn:
    key = conn.execute("SELECT week_key FROM hub_welfare_week_quota ORDER BY week_key DESC LIMIT 1").fetchone()[0]
    quotas = conn.execute("SELECT quotas_json FROM hub_welfare_week_quota WHERE week_key=?", (key,)).fetchone()[0]
    qd = _json.loads(quotas)
    if isinstance(qd, dict) and isinstance(qd.get("quotas"), dict):
        qd["quotas"]["xp"] = 10   # 新格式：额度嵌在 quotas 下
    else:
        qd["xp"] = 10             # 旧格式：扁平 dict
    conn.execute("UPDATE hub_welfare_week_quota SET quotas_json=? WHERE week_key=?", (_json.dumps(qd), key))
    conn.commit()
r = admin.post("/admin/welfare", data={
    "recipient_id": alpha_id, "xp": WELFARE_SINGLE_LIMITS["xp"], "spirit_stone": 0,
    "_csrf": csrf(admin, "/admin"),
})
msg = re.search(r'<div class="flash[^"]*">([^<]*)</div>', r.text).group(1)
assert "剩余额度" in msg and "不足以再发" in msg, msg

# ── 5) 聊天红包：托管 + 全员聊天提示 + 领取 ──
with connect_hub() as conn:
    transact_asset(conn, alpha_id, "spirit_stone", 500, "测试垫资")
r = alpha.post("/chat/red-packet", data={
    "asset_key": "spirit_stone", "total_amount": 100, "shares": 4,
    "note": "恭喜发财", "_csrf": csrf(alpha, "/chat"),
})
assert "红包已发到聊天" in r.text, r.text[:300]
page_u2 = beta.get("/chat").text
assert "🧧 成员甲 在聊天发出灵石红包 #" in page_u2 and "灵石红包" in page_u2 and "成员甲" in page_u2, "全员提示必须在聊天流中"
with connect_hub() as conn:
    pid = conn.execute(
        "SELECT id FROM hub_red_packets WHERE status='open' ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    assert balances(conn, alpha_id)["spirit_stone"] <= 500 - 100 + 50, "发出时应托管"
# 另一成员领取
r = beta.post(f"/market/group-red-packets/{pid}/claim", data={"_csrf": csrf(beta, "/market")})
assert "抢到" in r.text, r.text[:300]
with connect_hub() as conn:
    assert balances(conn, beta_id)["spirit_stone"] >= 1

# ── 6) 论坛红包：红包帖 + 全员聊天提示 ──
r = alpha.post("/forum/red-packet", data={
    "title": "庆祝组会通过", "body": "谢谢大家", "asset_key": "spirit_stone",
    "total_amount": 50, "shares": 5, "_csrf": csrf(alpha, "/forum"),
})
assert "红包帖已发布" in r.text, r.text[:300]
page_u2 = beta.get("/forum").text
assert "[红包] 庆祝组会通过" in page_u2
page_u2 = beta.get("/chat").text
assert "在论坛发布灵石红包帖" in page_u2, "论坛红包也要全员提示"
with connect_hub() as conn:
    assert conn.execute(
        "SELECT COUNT(*) n FROM hub_red_packets WHERE note='庆祝组会通过'"
    ).fetchone()["n"] == 1

# ── 7) 下一周额度重新随机刷新（不同 week_key）──
with connect_hub() as conn:
    next_week = datetime.now().astimezone() + timedelta(days=7)
    q2 = weekly_quotas(conn, now=next_week)
    key2 = None
    import json as _json
    row = conn.execute(
        "SELECT quotas_json FROM hub_welfare_week_quota ORDER BY created_at DESC"
    ).fetchall()
    keys = [r[0] for r in conn.execute("SELECT week_key FROM hub_welfare_week_quota")]
assert len(keys) >= 2, f"跨周应生成新额度行: {keys}"
print("welfare weekly + red packet pass")
'''
        with tempfile.TemporaryDirectory(prefix="welfare-weekly-") as directory:
            env = os.environ.copy()
            env["RESEARCH_OS_DATA_DIR"] = directory
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=300,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-3000:])


if __name__ == "__main__":
    unittest.main()
