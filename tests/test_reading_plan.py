from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReadingPlanTests(unittest.TestCase):
    """SPECS → 碳水泥阅读计划：种子、建档、统一记录、随手记与阶段验收。"""

    def test_plan_flow(self) -> None:
        script = r'''
from fastapi.testclient import TestClient

from core.database import connect, init_db
from services import reading_plan
from web.app import app

init_db()
plan = reading_plan.ensure_default_plan()
assert plan["title"] == "SPECS 方法 → 碳水泥：阅读清单与验收"

payload = reading_plan.plan_payload()
assert len(payload["entries"]) == 8, len(payload["entries"])
assert payload["entries"][0]["label"].startswith("01 Dupont & Donne (2015)")
assert payload["entries"][7]["label"].startswith("08 Stefaniuk et al. (2025)")
assert len(payload["reminders"]) == 2
assert len(payload["stages"]) == 3
# 阶段划分：1-3 / 4-6 / 7-8
assert [m["seq"] for m in payload["stages"][0]["members"]] == [1, 2, 3]
assert [m["seq"] for m in payload["stages"][2]["members"]] == [7, 8]

client = TestClient(app)

# 页面：原则、两条红线、八个条目都在
page = client.get("/reading/plan")
assert page.status_code == 200
for marker in ("SPECS 方法 → 碳水泥", "两条红线", "01 Dupont", "08 Stefaniuk",
               "不要直接把拟合时间常数等同于孔径", "随手记"):
    assert marker in page.text, marker

# 建档 → 三遍读工作台出现同名论文
created = client.post("/reading/plan/1/link", data={"work_id": 0}, follow_redirects=False)
assert created.status_code == 303
work_id = created.headers["location"].split("work_id=")[1]
work_page = client.get(f"/reading?work_id={work_id}")
assert "01 Dupont &amp; Donne (2015)" in work_page.text

# 统一记录：五栏保存并回显
saved = client.post(
    f"/reading/plan/unified/{work_id}",
    data={
        "question": "SPECS 如何从电流响应得到储能参数（p.5, Fig.3）",
        "setup": "恒电位阶跃，0.5 M Na2SO4",
        "formulas": "i(t)=..., tau=...",
        "evidence": "证据A/B；未解决问题：tau 与孔径的映射",
        "transfer": "方法可迁移；参数解释需在碳水泥重测",
    },
    follow_redirects=False,
)
assert saved.status_code == 303
page = client.get("/reading/plan").text
assert "p.5, Fig.3" in page
assert "阶段 1" in page

# 随手记：三类各记一条，「问题」进入待解决清单
for kind, text in (("point", "电位阶跃宽度影响拟合窗口"),
                   ("insight", "拟合初值来自 charging curve"),
                   ("question", "时间常数与孔径到底什么关系？")):
    note = client.post("/reading/plan/notes", data={"work_id": work_id, "kind": kind, "content": text},
                       follow_redirects=False)
    assert note.status_code == 303
page = client.get("/reading/plan").text
assert "电位阶跃宽度影响拟合窗口" in page
assert "时间常数与孔径到底什么关系？" in page

with connect() as conn:
    kinds = [r[0] for r in conn.execute(
        "SELECT kind FROM reading_notes WHERE work_id=?", (work_id,)
    ).fetchall()]
assert sorted(kinds) == ["insight", "point", "question"], kinds

# 状态推进 + 阶段进度：仅 01 完成时阶段 1 应为 33%
status = client.post("/reading/plan/1/status", data={"status": "done"}, follow_redirects=False)
assert status.status_code == 303
payload = reading_plan.plan_payload()
stage1 = payload["stages"][0]
assert stage1["progress"] == 33, stage1["progress"]
assert stage1["done"] is False
assert payload["entries"][0]["complete"] is True

# 三遍读工作台的提醒条
work_page = client.get(f"/reading?work_id={work_id}").text
assert "SPECS → 碳水泥阅读计划进行中" in client.get("/reading").text
print("plan pass")
'''
        with tempfile.TemporaryDirectory(prefix="reading-plan-") as directory:
            env = os.environ.copy()
            env["RESEARCH_OS_DATA_DIR"] = directory
            result = subprocess.run(
                [sys.executable, "-c", script], cwd=ROOT, env=env,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("plan pass", result.stdout)


if __name__ == "__main__":
    unittest.main()
