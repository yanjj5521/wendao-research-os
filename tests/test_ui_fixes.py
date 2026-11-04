from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class UiFixTests(unittest.TestCase):
    """本轮五个问题的回归：福利升级、多灵兽、静修切换、今日与协作排版。"""

    def test_pages_and_limits(self) -> None:
        script = r'''
import re
import sqlite3

from fastapi.testclient import TestClient

from core.database import init_db
from web.app import app

init_db()
client = TestClient(app)

# 1) 今日学习：新结构存在，且不再有劝退式文案
daily = client.get("/daily")
assert daily.status_code == 200, daily.status_code
for marker in ("交付打卡", "今天怎么样", "只想留一句下一步？"):
    assert marker in daily.text, marker
assert "任务只是一种可选激励" not in daily.text

# 2) AI 协作：页面级字号容器 + 精简后的标题
assistant = client.get("/assistant")
assert assistant.status_code == 200
assert 'class="assistant-page"' in assistant.text
assert "提示词" in assistant.text and "把结果收回系统" in assistant.text

# 3) 静修：全屏但仍可切换页面
idle = client.get("/idle")
assert idle.status_code == 200
for link in ('/daily', '/plans', '/review'):
    assert link in idle.text, link
assert "可随时切到其他页面" in idle.text

# 4) 灵兽园：容量 12，可养多只
adopt_a = client.post("/pets/adopt", data={"species_id": "dewfrog", "nickname": "小露"})
assert adopt_a.status_code == 200 and "灵兽" in adopt_a.text
adopt_b = client.post("/pets/adopt", data={"species_id": "umbrae", "nickname": "影子"})
assert "需要通过野外" in adopt_b.text or "已经是你的伙伴" in adopt_b.text

page = client.get("/pets").text
assert "/12" in page, "页头应显示灵兽袋容量"
from services.roam_pets import storage
from services.roam_pets.service import MAX_TEAM
assert MAX_TEAM == 12
saved = storage.load()
assert len(saved["pets"]) >= 1
print("ui fixes pass")
'''
        with tempfile.TemporaryDirectory(prefix="ui-fix-") as directory:
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
            self.assertIn("ui fixes pass", result.stdout)


if __name__ == "__main__":
    unittest.main()
