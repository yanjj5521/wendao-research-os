from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class HubReleaseDownloadTests(unittest.TestCase):
    """版本包必须能下载：登录态、Token 链接、路径迁移、文件缺失四种情况。"""

    def test_release_download_paths(self) -> None:
        script = r'''
import hashlib
import io
import re
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient
from hub.app import app
from hub.db import HUB_RELEASE_DIR, connect_hub, create_user, init_hub_db, now_iso

init_hub_db()

def build_zip(path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("portable.flag", "1")
        archive.writestr("使用说明.txt", "说明")
        archive.writestr("WendaoResearchV3.exe", b"MZ" + b"0" * 256)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buf.getvalue())

with connect_hub() as conn:
    user_id, token = create_user(conn, "releaser", "Passw0rd!123", "发布者", "admin")
    conn.commit()

package = HUB_RELEASE_DIR / "Team-Update-9.9.9.zip"
build_zip(package)
digest = hashlib.sha256(package.read_bytes()).hexdigest()
with connect_hub() as conn:
    conn.execute(
        "INSERT INTO hub_releases(version,title,notes,file_path,file_size,sha256,created_by,created_at) VALUES (?,?,?,?,?,?,?,?)",
        ("9.9.9", "测试版本包", "下载用例", str(package.resolve()), package.stat().st_size, digest, user_id, now_iso()),
    )
    conn.commit()
    release_id = int(conn.execute("SELECT id FROM hub_releases ORDER BY id DESC LIMIT 1").fetchone()["id"])

client = TestClient(app)
raw = package.read_bytes()

# 1) 没有登录、没有 Token → 拿不到文件（重定向到登录页，不泄露内容）
anon = client.get(f"/releases/{release_id}/download", follow_redirects=False)
assert anon.status_code in {303, 401}, anon.status_code
assert anon.headers.get("content-type", "").startswith("text/plain") or anon.status_code == 303
assert raw not in anon.content

# 2) 仅带 Token 的下载链接（桌面内嵌窗口 / 发给同事都能用）
by_token = client.get(f"/releases/{release_id}/download?token={token}", follow_redirects=False)
assert by_token.status_code == 200, by_token.status_code
assert by_token.content == raw
assert by_token.headers["content-type"].startswith("application/zip")
assert by_token.headers.get("accept-ranges") == "bytes"

# 3) 登录态下载
page = client.get("/")
csrf = re.search(r'name="_csrf" value="([^"]+)"', page.text).group(1)
login = client.post("/login", data={"username": "releaser", "password": "Passw0rd!123", "_csrf": csrf}, follow_redirects=False)
assert login.status_code == 303
by_cookie = client.get(f"/releases/{release_id}/download", follow_redirects=False)
assert by_cookie.status_code == 200
assert by_cookie.content == raw

# 4) 成员端 API 下载（Bearer Token）
api = client.get(f"/api/v1/releases/{release_id}/download", headers={"Authorization": f"Bearer {token}"}, follow_redirects=False)
assert api.status_code == 200 and api.content == raw

# 5) 数据目录搬过家：数据库里存的是旧绝对路径，但文件还在 hub_releases 里 → 仍可下载
with connect_hub() as conn:
    conn.execute("UPDATE hub_releases SET file_path=? WHERE id=?", (r"D:\moved\away\Team-Update-9.9.9.zip", release_id))
    conn.commit()
moved = client.get(f"/releases/{release_id}/download", follow_redirects=False)
assert moved.status_code == 200 and moved.content == raw, moved.status_code

# 6) 文件真的丢了 → 404 且给出可执行的原因
package.unlink()
missing = client.get(f"/releases/{release_id}/download?token={token}")
assert missing.status_code == 404
assert "重新上传" in missing.text
print("release download pass")
'''
        with tempfile.TemporaryDirectory(prefix="release-dl-") as directory:
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
            self.assertIn("release download pass", result.stdout)


if __name__ == "__main__":
    unittest.main()
