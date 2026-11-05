from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleaseEndToEndTests(unittest.TestCase):
    """管理员上传安装包 → 成员取包 → 校验结构，完整走一遍真实 HTTP。"""

    def test_upload_then_member_install(self) -> None:
        script = r'''
import io
import json
import re
import socket
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

import uvicorn
from fastapi.testclient import TestClient

from core.database import init_db, set_setting
from hub.app import app as hub_app
from hub.db import HUB_ADMIN_PATH, create_user, hub_transaction, init_hub_db
from services import release_feed
from services.team_update import validate_package

init_db()
init_hub_db()

def package_bytes(version: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("portable.flag", "1")
        archive.writestr("使用说明.txt", "说明")
        archive.writestr("WendaoResearchV3.exe", b"MZ" + b"0" * 4096)
    return buf.getvalue()

raw = package_bytes("9.9.9")

# 1) 管理员上传
client = TestClient(hub_app, base_url="http://hubserver", root_path="")
password = re.search(r"密码: (.+)", HUB_ADMIN_PATH.read_text(encoding="utf-8")).group(1).strip()
csrf = re.search(r'name="_csrf" value="([^"]+)"', client.get("/").text).group(1)
login = client.post("/login", data={"username": "admin", "password": password, "_csrf": csrf}, follow_redirects=False)
assert login.status_code == 303, login.status_code
csrf = re.search(r'name="_csrf" value="([^"]+)"', client.get("/admin").text).group(1)
upload = client.post(
    "/admin/releases",
    data={"version": "9.9.9", "title": "端到端测试包", "notes": "", "_csrf": csrf},
    files={"file": ("Team-Update-9.9.9.zip", raw, "application/zip")},
    follow_redirects=False,
)
assert upload.status_code == 303, upload.text[:300]

with hub_transaction() as conn:
    member_id, token = create_user(conn, "e2emember", "Passw0rd!123", "安装测试成员")

# 2) 起一个真实 HTTP 服务，让成员端按 URL 取包
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
port = sock.getsockname()[1]
sock.close()
config = uvicorn.Config(hub_app, host="127.0.0.1", port=port, log_level="error")
server = uvicorn.Server(config)
thread = threading.Thread(target=server.run, daemon=True)
thread.start()
base = f"http://127.0.0.1:{port}"
for _ in range(80):
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=1) as response:
            if response.status == 200:
                break
    except Exception:
        time.sleep(0.1)
else:
    raise AssertionError("中心服务未启动")

# 3) 成员端：识别为成员，向中心取包
# 这台机器的 loopback 会被判定为"自己的中心"，测试里把它当作远端中心来验证成员路径。
from services import hub_identity
hub_identity.is_local_url = lambda url: False

set_setting("sync_provider", "legacy_hub")
set_setting("hub_url", base)
set_setting("hub_api_token", token)
release = release_feed.latest_release()
assert release and release["source"] == "center", release
assert release["version"] == "9.9.9"

package = release_feed.fetch_package(release)
assert package.is_file() and package.stat().st_size > 0
assert package.name.endswith("9.9.9-Team-Update.zip"), package.name
validate_package(package)  # 结构校验与自动更新同一套判定

# 4) 校验和必须与中心记录一致
import hashlib
assert hashlib.sha256(package.read_bytes()).hexdigest() == str(release["sha256"]).lower()

# 5) 中心端下载入口仍然可用（管理员自己或成员浏览器）
download = client.get(f"/releases/{release['id']}/download", follow_redirects=False)
assert download.status_code in (200, 303), download.status_code

server.should_exit = True
print("release e2e pass")
'''
        with tempfile.TemporaryDirectory(prefix="release-e2e-") as directory:
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
                timeout=180,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("release e2e pass", result.stdout)


if __name__ == "__main__":
    unittest.main()
