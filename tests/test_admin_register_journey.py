from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AdminRegisterJourneyTests(unittest.TestCase):
    def test_admin_invite_register_journey(self) -> None:
        script = r'''
import re
from urllib.parse import quote

from fastapi.testclient import TestClient
from hub.app import app
from hub.db import HUB_ADMIN_PATH, connect_hub, init_hub_db

init_hub_db()
password = re.search(r"密码: (.+)", HUB_ADMIN_PATH.read_text(encoding="utf-8")).group(1).strip()

def csrf(client, path):
    page = client.get(path)
    return re.search(r'name="_csrf" value="([^"]+)"', page.text).group(1)

def flash(text):
    m = re.search(r'<div class="flash[^"]*">([^<]*)</div>', text)
    return m.group(1) if m else ""

admin = TestClient(app)
assert admin.post("/login", data={"username": "admin", "password": password, "_csrf": csrf(admin, "/")}).status_code == 200
admin.post("/admin/invites", data={"uses": 5, "days": 7, "_csrf": csrf(admin, "/admin")})
admin_page = admin.get("/admin").text
link = re.search(r'href="([^"]*/register\?invite=[^"]+)"', admin_page).group(1)
assert "成员名额" in admin_page and "调整成员上限" in admin_page

# 管理员登录态下打开注册页：新账号会加入切换列表，但当前管理员不会退出
reg_page = admin.get(link).text
assert "当前已登录" in reg_page and "当前账号不会退出" in reg_page
assert "账号切换列表" in reg_page and "管理员" in reg_page

# 提交注册：管理员仍是当前账号，新成员加入切换列表
code = link.split("invite=")[1]
r = admin.post("/register", data={
    "_csrf": csrf(admin, link), "invite": code, "username": "newmember",
    "display_name": "新成员", "password": "Passw0rd!", "password_confirm": "Passw0rd!",
})
assert r.url.path == "/", r.text[:300]
assert "账号 newmember 已创建并加入右上角账号切换列表" in r.text, r.text[:300]
assert "当前仍以" in r.text and "洞府主人" in r.text
with connect_hub() as conn:
    names = [x[0] for x in conn.execute("select username from accounts order by username")]
assert {"admin", "newmember"} <= set(names)

# 管理员身份保持有效，且切换菜单同时显示两个账号
admin_page = admin.get("/admin")
assert admin_page.status_code == 200
assert "管理控制台" in admin_page.text
assert "@newmember" in admin_page.text

# 收紧上限到 2（admin+newmember 已满）→ 注册第三人的提示给出调整入口
admin.post("/admin/settings", data={"max_members": 2, "_csrf": csrf(admin, "/admin")})
third = TestClient(app)
page = third.get("/register?invite=" + quote(code)).text
token = re.search(r'name="_csrf" value="([^"]+)"', page).group(1)
r = third.post("/register", data={"_csrf": token, "invite": code, "username": "third",
                                  "display_name": "第三人", "password": "Passw0rd!", "password_confirm": "Passw0rd!"})
msg = flash(r.text)
assert "已达到当前上限（2）" in msg and "调整成员上限" in msg, msg

# 管理员上调到 3 → 注册立即跑通
admin.post("/admin/settings", data={"max_members": 3, "_csrf": csrf(admin, "/admin")})
r = third.post("/register", data={"_csrf": csrf(third, "/register"), "invite": code, "username": "third",
                                  "display_name": "第三人", "password": "Passw0rd!", "password_confirm": "Passw0rd!"})
assert r.url.path == "/me", r.text[:300]
assert "账号 third 已创建并自动登录" in r.text
print("admin register journey pass")
'''
        with tempfile.TemporaryDirectory(prefix="admin-journey-") as directory:
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
