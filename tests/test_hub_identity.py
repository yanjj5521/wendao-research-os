from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class HubIdentityTests(unittest.TestCase):
    """同行会身份分流：自己开中心 vs 连接他人中心，入口必须分开。"""

    def test_identity_routes(self) -> None:
        script = r'''
import re
from fastapi.testclient import TestClient
from web.app import app
from core.database import init_db, set_setting
init_db()
client = TestClient(app)

def reset():
    set_setting("sync_provider", "disabled")
    set_setting("hub_url", "")
    set_setting("hub_api_token", "")

# --- 1) 未选择身份：/team 不再直接给登录页，而是先让人选身份 ---
reset()
team = client.get("/team/")
assert team.status_code == 200, team.status_code
assert "选择你的同行会身份" in team.text
assert "这台电脑当中心" in team.text and "连接别人的中心" in team.text
assert 'name="password"' not in team.text, "未选身份时不应出现登录表单"

online = client.get("/online")
assert online.status_code == 200
assert "还没选择身份" in online.text
assert "本机地址＝自己的中心，对方地址＝别人的中心" in online.text
assert "选择身份" in online.text

# --- 2) 成员身份：入口指向对方中心，本机副本被拦住 ---
set_setting("sync_provider", "legacy_hub")
set_setting("hub_url", "http://192.168.50.20:5050")
set_setting("hub_api_token", "token-for-test")
team = client.get("/team/")
assert team.status_code == 200
assert "你是成员" in team.text
assert "http://192.168.50.20:5050" in team.text
assert "进入同行会（192.168.50.20:5050）" in team.text, "按钮必须写明要打开的中心"
assert 'name="password"' not in team.text, "成员身份不应看到本机中心的登录表单"

online = client.get("/online")
assert "我是成员" in online.text
assert "http://192.168.50.20:5050" in online.text
assert "进入同行会（192.168.50.20:5050）" in online.text
assert "改连接" in online.text, "成员需要一条明确的改连接入口"
assert "打开发布页" in online.text, "寻找新版本的地方要能直接跳到发布页"
assert "本机地址＝自己的中心" in online.text

enter = client.get("/online/enter", follow_redirects=False)
assert enter.status_code == 303, enter.status_code
assert enter.headers["location"] == "http://192.168.50.20:5050", enter.headers["location"]

# 逃生门：显式 ?local=1 仍然能看到本机副本（排查用）
local = client.get("/team/?local=1")
assert local.status_code == 200
assert "选择你的同行会身份" not in local.text

# --- 3) 中心身份（地址指向本机）：/team 交回同行会应用 ---
set_setting("hub_url", "http://127.0.0.1:5050")
team = client.get("/team/")
assert team.status_code == 200
assert "选择你的同行会身份" not in team.text and "你是成员" not in team.text
online = client.get("/online")
assert "我是中心" in online.text or "由本机承载同行会" in online.text
assert "进入同行会" in online.text
print("hub identity pass")
'''
        with tempfile.TemporaryDirectory(prefix="hub-identity-") as directory:
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
            self.assertIn("hub identity pass", result.stdout)


if __name__ == "__main__":
    unittest.main()
