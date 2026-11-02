from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InviteRegistrationFlowTests(unittest.TestCase):
    def test_admin_and_member_registration_flows(self) -> None:
        script = r'''
import re
from urllib.parse import quote

from fastapi.testclient import TestClient
from hub.app import app
from hub.db import HUB_ADMIN_PATH, connect_hub, hub_instance_id, init_hub_db

init_hub_db()
password = re.search(r"密码: (.+)", HUB_ADMIN_PATH.read_text(encoding="utf-8")).group(1).strip()

def csrf(client, path):
    page = client.get(path)
    value = re.search(r'name="_csrf" value="([^"]+)"', page.text)
    assert value, page.text[:300]
    return value.group(1)

admin = TestClient(app)
login = admin.post(
    "/login",
    data={"username": "admin", "password": password, "_csrf": csrf(admin, "/")},
)
assert login.status_code == 200
created = admin.post(
    "/admin/invites",
    data={"uses": 6, "days": 7, "_csrf": csrf(admin, "/admin")},
)
assert created.status_code == 200
with connect_hub() as conn:
    code = conn.execute(
        "SELECT code FROM hub_invites ORDER BY rowid DESC LIMIT 1"
    ).fetchone()["code"]
    instance = hub_instance_id(conn)
assert len(code) == 10, code
assert not set(code) & set("IO01"), code

def register(username: str, invite_value: str, email: str = "", display: str = "", password: str = "Passw0rd!"):
    client = TestClient(app)
    page = client.get("/register?invite=" + quote(code))
    token = re.search(r'name="_csrf" value="([^"]+)"', page.text).group(1)
    return client.post(
        "/register",
        data={
            "invite": invite_value,
            "username": username,
            "display_name": display or username.title(),
            "email": email,
            "password": password,
            "password_confirm": password,
            "_csrf": token,
        },
    )

def uses() -> int:
    with connect_hub() as conn:
        return int(
            conn.execute(
                "SELECT uses_remaining FROM hub_invites WHERE code=?", (code,)
            ).fetchone()["uses_remaining"]
        )

# Admin registration attempt: the seeded admin exists, so the form must explain
# how to recover it instead of consuming the invite or failing vaguely.
admin_try = register("admin", code)
assert admin_try.status_code == 200, admin_try.text[:500]
assert "首次启动时自动创建的管理员账号" in admin_try.text, admin_try.text[:500]
assert uses() == 6, uses()

# Server-side validation rejects malformed or oversized fields before consuming the invite.
bad_email = register("badmailuser", code, email="not-an-email")
assert "邮箱格式不正确" in bad_email.text, bad_email.text[:500]
long_display = register("longdisplay", code, display="名" * 41)
assert "显示名称不能超过40" in long_display.text, long_display.text[:500]
long_password = register("longpassword", code, password="x" * 129)
assert "密码不能超过128" in long_password.text, long_password.text[:500]
assert uses() == 6, uses()
with connect_hub() as conn:
    blocked = conn.execute(
        "SELECT COUNT(*) n FROM accounts WHERE username IN ('badmailuser','longdisplay','longpassword')"
    ).fetchone()["n"]
assert blocked == 0, blocked

# Member registration accepts bare / lowercase / full URL / labelled inputs.
variants = (
    ("rawuser", code, "raw@example.com"),
    ("loweruser", code.lower(), ""),
    ("urluser", f"http://testserver/register?invite={code}", ""),
    ("labeluser", f"邀请码：{code}。", ""),
)
for username, invite_value, email in variants:
    response = register(username, invite_value, email)
    assert response.status_code == 200, (username, response.text[:500])
    assert response.url.path == "/me", (username, response.url)
    assert username.title() in response.text, username
assert uses() == 2, uses()

# Duplicate username / email are reported accurately and never consume a use.
dup_name = register("rawuser", code)
assert "已被使用" in dup_name.text, dup_name.text[:500]
assert uses() == 2, uses()
dup_mail = register("maildup", code, email="raw@example.com")
assert "已被账号 rawuser 使用" in dup_mail.text, dup_mail.text[:500]
assert uses() == 2, uses()

missing = register("ghostuser", "ZZZZZZ")
assert "不存在或已失效" in missing.text, missing.text[:500]
assert instance in missing.text, missing.text[:500]
malformed = register("shortuser", "ABC")
assert "格式不正确" in malformed.text, malformed.text[:500]

# A one-use invite cannot be registered twice.
admin.post(
    "/admin/invites",
    data={"uses": 1, "days": 7, "_csrf": csrf(admin, "/admin")},
)
with connect_hub() as conn:
    once = conn.execute(
        "SELECT code FROM hub_invites ORDER BY rowid DESC LIMIT 1"
    ).fetchone()["code"]
first = register("onceuser", once)
assert first.url.path == "/me", first.text[:500]
second = register("twiceuser", once)
assert "已用完" in second.text, second.text[:500]

with connect_hub() as conn:
    accounts = [
        row["username"]
        for row in conn.execute("SELECT username FROM accounts ORDER BY username")
    ]
assert {"rawuser", "loweruser", "urluser", "labeluser", "onceuser"} <= set(accounts)
assert accounts.count("admin") == 1
print("invite registration flow pass")
'''
        with tempfile.TemporaryDirectory(prefix="hub-register-") as directory:
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
            self.assertEqual(result.returncode, 0, result.stderr[-3000:])


if __name__ == "__main__":
    unittest.main()
