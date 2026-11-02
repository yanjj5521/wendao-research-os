from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MultiAccountSessionTests(unittest.TestCase):
    def test_switch_add_logout_and_embedded_session_isolation(self) -> None:
        script = r'''
import re

from fastapi.testclient import TestClient
import web.app as web
from hub.app import app as hub_app
from hub.db import HUB_ADMIN_PATH, connect_hub, create_user, hub_transaction, init_hub_db

init_hub_db()
admin_password = re.search(r"密码: (.+)", HUB_ADMIN_PATH.read_text(encoding="utf-8")).group(1).strip()
with hub_transaction() as conn:
    member1_id, _ = create_user(conn, "memberone", "Passw0rd!", "成员一", email="member1@example.com")
    member2_id, _ = create_user(conn, "membertwo", "Passw0rd!", "成员二", email="member2@example.com")
with connect_hub() as conn:
    admin_id = int(conn.execute("SELECT id FROM accounts WHERE username='admin'").fetchone()["id"])

def csrf(client, path):
    page = client.get(path)
    match = re.search(r'name="_csrf" value="([^"]+)"', page.text)
    assert match, (path, page.status_code, page.text[:300])
    return match.group(1)

def login(client, username, password):
    return client.post(
        "/team/login",
        data={"username": username, "password": password, "_csrf": csrf(client, "/team/login")},
    )

client = TestClient(web.app)
first = login(client, "admin", admin_password)
assert first.status_code == 200 and first.url.path == "/team/"
assert "洞府主人" in first.text
assert 'href="/team/static/hub.css"' in first.text
assert client.get("/team/static/hub.css").status_code == 200
add_page = client.get("/team/login")
assert add_page.status_code == 200 and "添加另一个账号" in add_page.text
assert "当前会话不会被替换" in add_page.text

second = login(client, "memberone", "Passw0rd!")
assert second.status_code == 200 and second.url.path == "/team/"
assert "成员一" in second.text and "账号 2" in second.text
third = login(client, "membertwo", "Passw0rd!")
assert third.status_code == 200 and "账号 3" in third.text

switch_token = csrf(client, "/team/")
switch_admin = client.post(
    "/team/session/switch",
    data={"account_id": admin_id, "_csrf": switch_token},
)
assert switch_admin.status_code == 200
assert "洞府主人" in switch_admin.text and "管理总览" in switch_admin.text
assert client.get("/team/admin").status_code == 200

bad_switch = client.post(
    "/team/session/switch",
    data={"account_id": 999999, "_csrf": csrf(client, "/team/")},
)
assert "不在当前会话的切换列表中" in bad_switch.text

# Admin creates a member without losing the active admin session.
created = client.post(
    "/team/admin/invites",
    data={"uses": 3, "days": 7, "_csrf": csrf(client, "/team/admin")},
)
with connect_hub() as conn:
    invite = conn.execute("SELECT code FROM hub_invites ORDER BY rowid DESC LIMIT 1").fetchone()["code"]
reg_page = client.get(f"/team/register?invite={invite}")
assert "当前账号不会退出" in reg_page.text
registered = client.post(
    "/team/register",
    data={
        "invite": invite,
        "username": "newmember",
        "display_name": "新成员",
        "email": "newmember@example.com",
        "password": "Passw0rd!",
        "password_confirm": "Passw0rd!",
        "_csrf": csrf(client, f"/team/register?invite={invite}"),
    },
)
assert registered.status_code == 200 and registered.url.path == "/team/"
assert "加入右上角账号切换列表" in registered.text
assert "当前仍以 洞府主人 登录" in registered.text
assert "@newmember" in registered.text
with connect_hub() as conn:
    new_id = int(conn.execute("SELECT id FROM accounts WHERE username='newmember'").fetchone()["id"])
assert client.get("/team/admin").status_code == 200
switched_new = client.post(
    "/team/session/switch",
    data={"account_id": new_id, "_csrf": csrf(client, "/team/")},
)
assert switched_new.url.path == "/team/me" or switched_new.url.path == "/team/"
assert "新成员" in switched_new.text

# Logging out the active account falls back to another saved account.
active_before_logout = "新成员"
logout_token = csrf(client, "/team/me")
logged_out = client.post("/team/logout", data={"_csrf": logout_token})
assert logged_out.status_code == 200
assert "已退出账号 新成员" in logged_out.text
assert "当前切换到" in logged_out.text
assert client.get("/team/me").status_code == 200

# Logout-all clears every saved account.
logout_all = client.post("/team/logout/all", data={"_csrf": csrf(client, "/team/me")})
assert logout_all.status_code == 200
login_page = client.get("/team/")
assert "进入同行会" in login_page.text and "添加另一个账号" not in login_page.text

# Embedded Hub must not overwrite the personal session or consume its flash.
personal = TestClient(web.app)
buy = personal.post("/world/artifacts/qingxin_slip/buy", follow_redirects=False)
assert buy.status_code == 303
personal_cookie = personal.cookies.get("session")
assert personal_cookie
team = personal.get("/team/", follow_redirects=False)
assert team.status_code == 200
assert not any(header.startswith("session=") for header in team.headers.get_list("set-cookie"))
assert personal.cookies.get("session") == personal_cookie
world = personal.get("/world", follow_redirects=True)
assert "已获得法器" in world.text
print("multi-account and session isolation pass")
'''
        with tempfile.TemporaryDirectory(prefix="hub-multi-account-") as directory:
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
            self.assertEqual(result.returncode, 0, result.stderr[-5000:])


    def test_standalone_hub_multi_account_switch(self) -> None:
        script = r'''
import re
from fastapi.testclient import TestClient
from hub.app import app
from hub.db import create_user, hub_transaction, init_hub_db

init_hub_db()
with hub_transaction() as conn:
    first_id, _ = create_user(conn, "standaloneone", "Passw0rd!", "独立一号", email="standalone1@example.com")
    create_user(conn, "standalonetwo", "Passw0rd!", "独立二号", email="standalone2@example.com")

def csrf(client, path):
    page = client.get(path)
    return re.search(r'name="_csrf" value="([^"]+)"', page.text).group(1)

def login(client, username):
    return client.post("/login", data={"username": username, "password": "Passw0rd!", "_csrf": csrf(client, "/login")})

client = TestClient(app)
login_page = client.get("/login")
assert 'href="/static/hub.css"' in login_page.text
assert client.get("/static/hub.css").status_code == 200
assert login(client, "standaloneone").status_code == 200
assert login(client, "standalonetwo").status_code == 200
page = client.get("/")
assert "独立二号" in page.text and "账号 2" in page.text
switch = client.post("/session/switch", data={"account_id": first_id, "_csrf": csrf(client, "/")})
assert switch.status_code == 200 and "独立一号" in switch.text
assert client.post("/logout/all", data={"_csrf": csrf(client, "/")}).status_code == 200
assert "进入同行会" in client.get("/").text
print("standalone multi-account pass")
'''
        with tempfile.TemporaryDirectory(prefix="hub-standalone-multi-") as directory:
            env = os.environ.copy()
            env["RESEARCH_OS_DATA_DIR"] = directory
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=180,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-4000:])


if __name__ == "__main__":
    unittest.main()
