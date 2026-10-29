"""同行会管理端契约测试：统计、账号留存导出、头像存储与权限边界。"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(script: str) -> str:
    environment = dict(os.environ)
    environment["RESEARCH_OS_DATA_DIR"] = tempfile.mkdtemp(prefix="wendao-hub-admin-")
    environment["PYTHONPATH"] = str(ROOT)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"hub admin script failed:\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


ADMIN_FLOW = """
import io
import re
from fastapi.testclient import TestClient
from hub.app import app
from hub.db import HUB_ADMIN_PATH, INSTANCE_DIR, balances, connect_hub, create_user, init_hub_db

init_hub_db()
password = re.search(r"密码: (.+)", HUB_ADMIN_PATH.read_text(encoding="utf-8")).group(1).strip()
with connect_hub() as conn:
    member_id, _ = create_user(conn, "member", "member-password-123", "成员甲", email="member@example.com")
    conn.commit()

def csrf(client, path):
    return re.search(r'name="_csrf" value="([^"]+)"', client.get(path).text).group(1)

admin = TestClient(app)
admin.post("/login", data={"username": "admin", "password": password, "_csrf": csrf(admin, "/")})

# 1) 管理页：统计 + 账号留存信息 + 权限说明
page = admin.get("/admin")
assert page.status_code == 200
for marker in ("运营概览", "成员总数", "近 7 天新增", "今日在线", "导出账号留存 CSV",
               "member@example.com", "权限边界", "审计"):
    assert marker in page.text, marker

# 2) 账号留存 CSV：仅管理员可导出，内容含账号与资产
csv_response = admin.get("/admin/export/accounts.csv")
assert csv_response.status_code == 200
body = csv_response.content.decode("utf-8-sig")
assert "member@example.com" in body and "成员甲" in body and "灵石" in body, body[:200]

member = TestClient(app)
member.post("/login", data={"username": "member", "password": "member-password-123", "_csrf": csrf(member, "/")})
member_csv = member.get("/admin/export/accounts.csv")
assert member_csv.status_code == 403, member_csv.status_code
assert member.get("/admin").status_code == 403

# 3) 成员头像：上传 → 我的联机主页可见 → 管理员重置 → 文件消失
png = (b"\\x89PNG\\r\\n\\x1a\\n" + b"0" * 128)
upload = member.post(
    "/me/avatar",
    files={"file": ("avatar.png", png, "image/png")},
    data={"_csrf": csrf(member, "/me")},
    follow_redirects=False,
)
assert upload.status_code == 303, upload.status_code
avatar_files = list((INSTANCE_DIR / "hub_files" / "avatars").glob("u*"))
assert len(avatar_files) == 1, avatar_files
me_page = member.get("/me")
assert "media/avatars" in me_page.text

reset = admin.post(
    f"/admin/users/{member_id}/reset-avatar",
    data={"_csrf": csrf(admin, "/admin")},
    follow_redirects=False,
)
assert reset.status_code == 303
avatar_files = list((INSTANCE_DIR / "hub_files" / "avatars").glob("u*"))
assert len(avatar_files) == 0, avatar_files

# 4) 管理动作入审计
with connect_hub() as connection:
    actions = [row[0] for row in connection.execute("SELECT action FROM hub_audit_log ORDER BY id")]
assert "avatar_upload" in actions and "avatar_reset" in actions and "export_accounts" in actions
print("hub-admin-ok")
"""


class HubAdminTests(unittest.TestCase):
    def test_admin_stats_accounts_export_avatar_and_permissions(self) -> None:
        self.assertIn("hub-admin-ok", _run(ADMIN_FLOW))


if __name__ == "__main__":
    unittest.main()
