from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class EmbeddedRegisterHintTests(unittest.TestCase):
    """成员在"系统里面"用管理员中心邀请码注册 → 报错附带正确路径指引。"""

    def test_embedded_register_hint(self) -> None:
        script = r'''
import re
from fastapi.testclient import TestClient
from web.app import app
from core.database import init_db, set_setting
init_db()
# 成员身份：这台电脑连的是别人的中心
set_setting("sync_provider", "legacy_hub")
set_setting("hub_url", "http://192.168.60.10:5050")
set_setting("hub_api_token", "member-token")
c = TestClient(app)   # 个人端应用：/team 是成员本机的中心实例

# 1) 成员身份下 /team 不再放行本机副本，直接说明数据在对方中心
blocked = c.get("/team/register?invite=CF84ATQGAH")
assert blocked.status_code == 200
assert "你是成员" in blocked.text
assert "http://192.168.60.10:5050" in blocked.text
assert 'name="password"' not in blocked.text

# 2) 显式 ?local=1 打开本机副本后，注册失败仍然给出就地指引
r = c.get("/team/register?invite=CF84ATQGAH&local=1")
csrf = re.search(r'name="_csrf" value="([^"]+)"', r.text).group(1)
r2 = c.post("/team/register?local=1", data={
    "_csrf": csrf, "invite": "CF84ATQGAH", "username": "member1",
    "display_name": "成员一", "password": "Passw0rd!", "password_confirm": "Passw0rd!",
}, follow_redirects=False)
assert r2.status_code == 303, r2.status_code
msg = c.get("/team/?local=1").text
assert "邀请码不存在或已失效" in msg
assert "本机自带的中心实例" in msg          # 指出这是本机实例
assert "我的联机主页" in msg                # 给出正确路径：浏览器注册 → 复制 Token
assert "连接同行会" in msg

# 独立中心（管理员部署的那类，非内嵌挂载）不应出现该提示
from hub.app import app as hub_app
from hub.db import init_hub_db
init_hub_db()
h = TestClient(hub_app, root_path="", base_url="http://hubserver")
r = h.get("/register?invite=CF84ATQGAH")
csrf = re.search(r'name="_csrf" value="([^"]+)"', r.text).group(1)
r2 = h.post("/register", data={
    "_csrf": csrf, "invite": "CF84ATQGAH", "username": "member1",
    "display_name": "成员一", "password": "Passw0rd!", "password_confirm": "Passw0rd!",
})
msg2 = re.search(r'<div class="flash[^"]*">([^<]*)</div>', r2.text).group(1)
assert "邀请码不存在或已失效" in msg2
assert "本机自带的中心实例" not in msg2
print("embedded hint pass")
'''
        with tempfile.TemporaryDirectory(prefix="hint-") as directory:
            env = os.environ.copy()
            env["RESEARCH_OS_DATA_DIR"] = directory
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=240,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-3000:])


if __name__ == "__main__":
    unittest.main()
