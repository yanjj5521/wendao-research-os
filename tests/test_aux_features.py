from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AuxFeatureTests(unittest.TestCase):
    """PDF 辅助抽取、GitHub 下载回退、境界快照同步。"""

    def test_pdf_sketch(self) -> None:
        script = r'''
try:
    import pymupdf
except ImportError:  # 缺少构建 PDF 的库时跳过，不影响产品逻辑
    print("pdf skip")
    raise SystemExit(0)

from services import pdf_sketch

doc = pymupdf.open()
page = doc.new_page()
page.insert_text((72, 72), "A Three-Pass Approach to Reading Papers", fontsize=16)
page.insert_text((72, 110), "Figure 1: pipeline of the proposed method", fontsize=11)
page.insert_text((72, 130), "Table 2: comparison on three datasets", fontsize=11)
page2 = doc.new_page()
page2.insert_text((72, 72), "Conclusion", fontsize=14)
page2.insert_text(
    (72, 96),
    "The three-pass method reduces wasted reading time. Limitations include reliance on domain knowledge.",
    fontsize=11,
)
data = doc.tobytes()
doc.close()

result = pdf_sketch.sketch(data)
assert "Three-Pass" in result["title"], result["title"]
assert len(result["captions"]) >= 2, result["captions"]
assert "wasted reading time" in result["conclusion"], result["conclusion"]
assert result["pages"] == 2

try:
    pdf_sketch.sketch(b"not a pdf")
    raise AssertionError("损坏文件应被拒绝")
except ValueError:
    pass
try:
    pdf_sketch.sketch(b"")
    raise AssertionError("空文件应被拒绝")
except ValueError:
    pass
print("pdf pass")
'''
        with tempfile.TemporaryDirectory(prefix="pdf-") as directory:
            env = os.environ.copy()
            env["RESEARCH_OS_DATA_DIR"] = directory
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=ROOT, env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=120,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("pdf pass", result.stdout + "pdf skip")

    def test_github_fallback_and_proxy(self) -> None:
        script = r'''
import hashlib
import io
import zipfile
from pathlib import Path
from unittest.mock import patch

from core.database import init_db, set_setting
from services import github_update
from services.download import DownloadError

init_db()

# 代理只接受 http(s)，其余当直连
assert github_update.download_proxy() == ""
set_setting("github_download_proxy", "socks5://127.0.0.1:1080")
assert github_update.download_proxy() == ""
set_setting("github_download_proxy", "http://127.0.0.1:7890")
assert github_update.download_proxy() == "http://127.0.0.1:7890"

url = ("https://github.com/yanjj5521/wendao-research-os/releases/download/"
       "v1.0.0/WendaoResearch-v1.0.0-User.zip")
assert github_update.mirror_url(url, "https://ghproxy.net/") == f"https://ghproxy.net/{url}"

# 直连失败 → 镜像成功；返回的包必须通过 SHA-256
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as archive:
    archive.writestr("portable.flag", "1")
    archive.writestr("使用说明.txt", "说明")
    archive.writestr("WendaoResearchV3.exe", b"MZ" + b"0" * 4096)
payload = buf.getvalue()
digest = hashlib.sha256(payload).hexdigest()

calls: list[str] = []

def fake_fetch(target_url, destination, *, expected_sha256, max_bytes, timeout, headers=None, proxy=""):
    calls.append(target_url)
    if len(calls) == 1:
        raise DownloadError("直连超时")
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    Path(destination).write_bytes(payload)
    return Path(destination)

release = {
    "version": "1.0.0",
    "package_url": url,
    "package_name": "WendaoResearch-v1.0.0-User.zip",
    "package_sha256": digest,
    "checksum_url": "",
}
with patch.object(github_update, "fetch_resumable", side_effect=fake_fetch), \
     patch.object(github_update, "backup_local_db", return_value=Path("backup.db")), \
     patch.object(github_update, "_validate_user_package", return_value=None):
    saved = github_update.prepare_github_update(release)

assert len(calls) == 2, calls
assert calls[0] == url and calls[1].startswith("https://ghproxy.net/"), calls
assert Path(saved).is_file()

# 全部失败时给出可执行提示而不是裸异常
calls.clear()
def always_fail(*args, **kwargs):
    raise DownloadError("网络不可达")

release["package_sha256"] = "0" * 64
with patch.object(github_update, "fetch_resumable", side_effect=always_fail):
    try:
        github_update.prepare_github_update(release)
        raise AssertionError("全部失败时应抛错")
    except github_update.GithubUpdateError as exc:
        assert "镜像" in str(exc), str(exc)
print("github pass")
'''
        with tempfile.TemporaryDirectory(prefix="gh-") as directory:
            env = os.environ.copy()
            env["RESEARCH_OS_DATA_DIR"] = directory
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=ROOT, env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=120,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("github pass", result.stdout)

    def test_realm_snapshot_preferred(self) -> None:
        script = r'''
import re

from fastapi.testclient import TestClient

from hub.app import app
from hub.db import HUB_ADMIN_PATH, create_user, hub_transaction, init_hub_db

init_hub_db()
with hub_transaction() as conn:
    member_id, token = create_user(conn, "realmname", "Passw0rd!123", "境界快照成员")
    conn.execute(
        "INSERT INTO hub_asset_transactions(user_id,asset_key,amount,reason,created_at)"
        " VALUES (?,?,?,?,?)",
        (member_id, "cultivation", 950, "基础修为", "2026-01-01T00:00:00+00:00"),
    )

client = TestClient(app)
push = client.post(
    "/api/v1/profile/realm",
    json={"realm_name": "金丹三层", "realm_index": 14, "cultivation": 4321},
    headers={"Authorization": f"Bearer {token}"},
)
assert push.status_code == 200, push.text

password = re.search(r"密码: (.+)", HUB_ADMIN_PATH.read_text(encoding="utf-8")).group(1).strip()
csrf = re.search(r'name="_csrf" value="([^"]+)"', client.get("/").text).group(1)
client.post("/login", data={"username": "admin", "password": password, "_csrf": csrf}, follow_redirects=False)

members = client.get("/members").text
assert "金丹三层" in members, "成员页应优先显示个人端同步来的境界名"
assert "金丹三层" in client.get("/admin").text
print("realm snapshot pass")
'''
        with tempfile.TemporaryDirectory(prefix="realm2-") as directory:
            env = os.environ.copy()
            env["RESEARCH_OS_DATA_DIR"] = directory
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=ROOT, env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=120,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("realm snapshot pass", result.stdout)


if __name__ == "__main__":
    unittest.main()
