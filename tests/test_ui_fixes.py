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

    def test_wallpaper_and_nav(self) -> None:
        script = r'''
import io

from fastapi.testclient import TestClient
from PIL import Image

from core.database import init_db, get_setting
from web.app import app

init_db()
client = TestClient(app)

# 先把一张自定义壁纸存进本地壁纸库
buf = io.BytesIO()
Image.new("RGB", (64, 40), (120, 90, 60)).save(buf, format="PNG")
from services.idle_space import save_wallpaper_bytes
save_wallpaper_bytes(buf.getvalue())
assert get_setting("idle_wallpaper_kind", "") == "custom", get_setting("idle_wallpaper_kind", "")
custom_file = get_setting("idle_wallpaper_file", "")
assert custom_file

# 关键回归：已选自定义壁纸时，再选内置壁纸必须生效（旧版会被自定义覆盖）
resp = client.post("/idle/config", data={"layout": "vertical", "focus": "", "wallpaper": "builtin:dawn"}, follow_redirects=False)
assert resp.status_code == 303
assert get_setting("idle_wallpaper_kind") == "builtin", get_setting("idle_wallpaper_kind")
assert get_setting("idle_wallpaper_builtin") == "dawn"

# 再切回自定义
resp = client.post("/idle/config", data={"layout": "vertical", "focus": "", "wallpaper": f"custom:{custom_file}"}, follow_redirects=False)
assert resp.status_code == 303
assert get_setting("idle_wallpaper_kind") == "custom"

# 页面：单选组已合并为一个 name，并保留旧字段之外的新值格式
page = client.get("/idle").text
assert 'name="wallpaper"' in page
assert "builtin:" in page and "custom:" in page

# 今日页：下一步改成按钮 + hidden 表单，并有展开脚本
daily = client.get("/daily").text
assert "daily-quick-toggle" in daily and "aria-expanded" in daily
assert 'id="daily-quick-body"' in daily and "hidden" in daily
assert "quickBody.hidden = open" in daily

# 导航：分类与顺序符合推荐布局
from web.runtime import navigation_layout
layout = navigation_layout()
names = [group["key"] for group in layout]
assert names == ["cultivation", "research", "knowledge", "growth", "workspaces", "system"], names
first_group = layout[0]["items"]
assert [item["key"] for item in first_group] == ["dashboard", "daily", "plans", "review"]

# 恢复推荐分类按钮可用
reset = client.post("/settings/nav/reset", follow_redirects=False)
assert reset.status_code == 303
print("wallpaper and nav pass")
'''
        with tempfile.TemporaryDirectory(prefix="ui-fix2-") as directory:
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
            self.assertIn("wallpaper and nav pass", result.stdout)


if __name__ == "__main__":
    unittest.main()
