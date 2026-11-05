from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReadingAndRealmTests(unittest.TestCase):
    """论文三遍读工作台 + 同行会境界显示。"""

    def test_reading_flow(self) -> None:
        script = r'''
from fastapi.testclient import TestClient

from core.database import connect, init_db
from web.app import app

init_db()
client = TestClient(app)

page = client.get("/reading")
assert page.status_code == 200
assert "论文三遍读" in page.text and "第一遍 · 五个 C" not in page.text

created = client.post(
    "/reading/new",
    data={"title": "Three-Pass Approach", "venue": "SIGCOMM", "year": "2007"},
    follow_redirects=False,
)
assert created.status_code == 303
work_id = created.headers["location"].split("work_id=")[1]

saved = client.post(
    f"/reading/{work_id}/save",
    data={
        "title": "Three-Pass Approach",
        "authors": "S. Keshav",
        "venue": "SIGCOMM",
        "year": "2007",
        "category": "方法",
        "context": "文献阅读方法",
        "contribution": "三遍阅读法",
        "one_liner": "分层筛选，先定去留再深入",
        "method_note": "三遍各设目标与产出",
        "data_setup": "作者经验总结",
        "results": "被广泛采用",
        "weakness": "依赖领域熟悉度",
        "relevance": "可直接用于组会汇报",
        "pass3_note": "公式与代码变量一一对应；实现里多了 warmup。",
        "decision": "read",
        "scenario": "group",
    },
    follow_redirects=False,
)
assert saved.status_code == 303

page = client.get(f"/reading?work_id={work_id}").text
for marker in ("六段式汇报大纲", "行动清单", "问答准备", "文献卡" if "文献卡" in page else "第一遍 · 五个 C"):
    assert marker in page, marker
assert "1. 封面" in page and "6. 创新点与借鉴价值" in page
assert "场景侧重" in page

qa = client.post(
    f"/reading/{work_id}/qa",
    data={
        "qa_value": "方法可迁移",
        "qa_novelty": "分层筛选",
        "qa_method": "成本低",
        "qa_limits": "依赖领域",
        "qa_inspire": "用到组会",
    },
    follow_redirects=False,
)
assert qa.status_code == 303
with connect() as conn:
    raw = conn.execute("SELECT qa_json FROM reading_works WHERE id=?", (work_id,)).fetchone()[0]
assert "方法可迁移" in raw and "用到组会" in raw

stage = client.post(f"/reading/{work_id}/stage", data={"stage": "pass3"}, follow_redirects=False)
assert stage.status_code == 303
with connect() as conn:
    current = conn.execute("SELECT stage FROM reading_works WHERE id=?", (work_id,)).fetchone()[0]
assert current == "pass3"

page = client.get(f"/reading?work_id={work_id}").text
assert "完成度 100%" in page, [line for line in page.splitlines() if "完成度" in line][:1]

from services import reading_flow as flow

assert flow.stage_label("pass2") == "第二遍 · 抓骨架"
assert flow.next_stage("pass1") == "pass2"
print("reading pass")
'''
        with tempfile.TemporaryDirectory(prefix="reading-") as directory:
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
            self.assertIn("reading pass", result.stdout)

    def test_hub_realm_display(self) -> None:
        script = r'''
import re

from fastapi.testclient import TestClient

from hub.app import app
from hub.db import HUB_ADMIN_PATH, connect_hub, create_user, hub_transaction, init_hub_db, realm_label
from services.economy import transact  # noqa: F401  (保持与个人端一致的导入习惯)

init_hub_db()
with hub_transaction() as conn:
    member_id, _ = create_user(conn, "realmuser", "Passw0rd!123", "境界成员")
    conn.execute(
        "INSERT INTO hub_asset_transactions(user_id,asset_key,amount,reason,created_at)"
        " VALUES (?,?,?,?,?)",
        (member_id, "cultivation", 950, "境界测试", "2026-01-01T00:00:00+00:00"),
    )

assert realm_label(0)["name"] == "练气"
assert realm_label(950)["name"] == "金丹"
assert realm_label(950)["next"] == 2200

password = re.search(r"密码: (.+)", HUB_ADMIN_PATH.read_text(encoding="utf-8")).group(1).strip()
client = TestClient(app)
csrf = re.search(r'name="_csrf" value="([^"]+)"', client.get("/").text).group(1)
assert client.post("/login", data={"username": "admin", "password": password, "_csrf": csrf}, follow_redirects=False).status_code == 303

members = client.get("/members")
assert members.status_code == 200
assert "金丹" in members.text, "成员页应显示境界"

admin = client.get("/admin")
assert admin.status_code == 200
assert "境界" in admin.text and "金丹" in admin.text
print("realm pass")
'''
        with tempfile.TemporaryDirectory(prefix="realm-") as directory:
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
            self.assertIn("realm pass", result.stdout)


if __name__ == "__main__":
    unittest.main()
