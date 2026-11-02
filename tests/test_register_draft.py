from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RegisterDraftFlow(unittest.TestCase):
    """注册失败/关闭窗口后的草稿保留（服务端回填 + localStorage 脚本契约）。"""

    def test_flow(self) -> None:
        script = r'''
import re
from fastapi.testclient import TestClient
from hub.app import app
from hub.db import HUB_ADMIN_PATH, connect_hub, create_user, init_hub_db, hub_transaction
init_hub_db()
password = re.search(r"密码: (.+)", HUB_ADMIN_PATH.read_text(encoding="utf-8")).group(1).strip()
with hub_transaction() as conn:
    create_user(conn, "taken", "Passw0rd!", "已被占用")
client = TestClient(app)

def csrf(path="/register"):
    return re.search(r'name="_csrf" value="([^"]+)"', client.get(path).text).group(1)

def register(**overrides):
    data = {"invite": "ABC", "username": "newbie", "display_name": "新同学", "email": "me@example.com",
            "password": "Passw0rd!", "password_confirm": "Passw0rd!"}
    data.update(overrides)
    return client.post("/register", data={"_csrf": csrf(), **data})

r = register(invite="ABC")
assert "u=newbie" in str(r.url), r.url
page = client.get("/register?" + str(r.url).split("?", 1)[1]).text
assert 'value="newbie"' in page and 'value="新同学"' in page and 'value="me@example.com"' in page
assert "Passw0rd" not in str(r.url)

with connect_hub() as conn:
    conn.execute("INSERT INTO hub_invites(code,uses_remaining,expires_at,created_by,created_at) VALUES (?,?,?,1,?)",
                 ("DRAFTCODE1", 5, "2099-01-01T00:00:00+08:00", "2026-11-02T00:00:00+08:00"))
    conn.commit()
r = register(invite="DRAFTCODE1", username="taken")
assert "已被使用" in r.text, r.text[:300]
assert "u=taken" in str(r.url), r.url
page = client.get("/register?" + str(r.url).split("?", 1)[1]).text
assert 'value="taken"' in page and 'value="新同学"' in page

template = open("hub_templates/register.html", encoding="utf-8").read()
assert '<script src="/static/register.js" defer></script>' in page or '<script src="/team/static/register.js" defer></script>' in page
assert "<script>" not in template
register_js = open("hub_static/register.js", encoding="utf-8").read()
assert "password" not in register_js
assert "clearDraft" in template and "clearDraft" in register_js
page = client.get("/register").text
archived = open("docs/HUB_UI_COPY.md", encoding="utf-8").read()
assert "管理员账号无需注册" in page and "admin-path" not in page
assert "admin-path" in archived
print("register draft flow pass")

'''
        with tempfile.TemporaryDirectory(prefix="RegisterDraftFlow-") as directory:
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
