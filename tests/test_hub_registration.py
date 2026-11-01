from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InviteRegistrationFlowTests(unittest.TestCase):
    def test_admin_invite_accepts_bare_lowercase_url_and_label(self) -> None:
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
assert len(code) == 10, code
assert not set(code) & set("IO01"), code

def register(username: str, invite_value: str):
    client = TestClient(app)
    page = client.get("/register?invite=" + quote(code))
    token = re.search(r'name="_csrf" value="([^"]+)"', page.text).group(1)
    return client.post(
        "/register",
        data={
            "invite": invite_value,
            "username": username,
            "display_name": username.title(),
            "password": "Passw0rd!",
            "password_confirm": "Passw0rd!",
            "_csrf": token,
        },
    )

variants = (
    ("rawuser", code),
    ("loweruser", code.lower()),
    ("urluser", f"http://testserver/register?invite={code}"),
    ("labeluser", f"邀请码：{code}。"),
)
for username, invite_value in variants:
    response = register(username, invite_value)
    assert response.status_code == 200, (username, response.text[:500])
    assert response.url.path == "/me", (username, response.url)
    assert username.title() in response.text, username

with connect_hub() as conn:
    remaining = conn.execute(
        "SELECT uses_remaining FROM hub_invites WHERE code=?", (code,)
    ).fetchone()["uses_remaining"]
    accounts = [
        row["username"]
        for row in conn.execute("SELECT username FROM accounts ORDER BY username")
    ]
assert remaining == 2, remaining
assert {"rawuser", "loweruser", "urluser", "labeluser"} <= set(accounts)

malformed = register("badshort", "ABC")
assert "格式不正确" in malformed.text, malformed.text[:500]
missing = register("badmissing", "ZZZZZZ")
assert "不存在或已失效" in missing.text, missing.text[:500]

used = admin.post(
    "/admin/invites",
    data={"uses": 1, "days": 7, "_csrf": csrf(admin, "/admin")},
)
assert used.status_code == 200
with connect_hub() as conn:
    once = conn.execute(
        "SELECT code FROM hub_invites ORDER BY rowid DESC LIMIT 1"
    ).fetchone()["code"]
first = register("onceuser", once)
assert first.url.path == "/me", first.text[:500]
second = register("twiceuser", once)
assert "已用完" in second.text, second.text[:500]
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
