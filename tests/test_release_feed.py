from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleaseFeedTests(unittest.TestCase):
    """版本包信息与取包：管理员读本机中心，成员问对方中心，断网退回缓存。"""

    def test_release_feed_host_and_member(self) -> None:
        script = r'''
import hashlib
import io
import zipfile
from unittest.mock import patch
from pathlib import Path

from core.database import init_db, set_setting, connect
from hub.db import HUB_RELEASE_DIR, connect_hub, create_user, init_hub_db, now_iso
from services import release_feed

init_db()
init_hub_db()

def build_zip(path: Path, valid: bool = True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        if valid:
            archive.writestr("portable.flag", "1")
            archive.writestr("使用说明.txt", "说明")
            archive.writestr("WendaoResearchV3.exe", b"MZ" + b"0" * 256)
        else:
            archive.writestr("readme.txt", "not a package")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buf.getvalue())
    return path.read_bytes()

with connect_hub() as conn:
    admin_id, _ = create_user(conn, "feedadmin", "Passw0rd!123", "包管理员", "admin")
    conn.commit()

package = HUB_RELEASE_DIR / "Team-Update-1.11.1.zip"
raw = build_zip(package)
digest = hashlib.sha256(raw).hexdigest()
with connect_hub() as conn:
    conn.execute(
        "INSERT INTO hub_releases(version,title,notes,file_path,file_size,sha256,created_by,created_at) VALUES (?,?,?,?,?,?,?,?)",
        ("1.11.1", "测试包", "", str(package.resolve()), package.stat().st_size, digest, admin_id, now_iso()),
    )
    conn.commit()

# 管理员：地址指向本机 = 中心身份，直接读本机中心库
set_setting("sync_provider", "legacy_hub")
set_setting("hub_url", "http://127.0.0.1:5050")
set_setting("hub_api_token", "self-token")
release = release_feed.latest_release()
assert release and release["source"] == "local", release
assert release["version"] == "1.11.1"

taken = release_feed.fetch_package(release)
assert taken.is_file(), taken
assert hashlib.sha256(taken.read_bytes()).hexdigest() == digest
assert str(taken).endswith("WendaoResearch-v1.11.1-Team-Update.zip")

# 结构不合法的包必须被拒绝，并删除落盘文件
bad = HUB_RELEASE_DIR / "Team-Update-9.9.9.zip"
bad_raw = build_zip(bad, valid=False)
with connect_hub() as conn:
    conn.execute(
        "INSERT INTO hub_releases(version,title,notes,file_path,file_size,sha256,created_by,created_at) VALUES (?,?,?,?,?,?,?,?)",
        ("9.9.9", "坏包", "", str(bad.resolve()), bad.stat().st_size, hashlib.sha256(bad_raw).hexdigest(), admin_id, now_iso()),
    )
    conn.commit()
bad_release = release_feed.latest_release()
assert bad_release["version"] == "9.9.9"
try:
    release_feed.fetch_package(bad_release)
    raise AssertionError("坏包不应通过校验")
except ValueError:
    pass
assert not list(release_feed.DOWNLOAD_DIR.glob("*9.9.9*")), "坏包文件必须被清除"

# 成员：对方中心不可达 → 退回同步缓存，且标明来源是缓存
set_setting("hub_url", "http://192.0.2.10:5050")
from services.online_sync import _cache
with connect() as conn:
    _cache(conn, "latest_release", {"id": 7, "version": "1.11.1", "sha256": digest})
    conn.commit()
with patch.object(release_feed, "_remote_latest", side_effect=OSError("unreachable")):
    cached = release_feed.latest_release()
assert cached and cached["source"] == "cache", cached
assert cached["version"] == "1.11.1"
print("release feed pass")
'''
        with tempfile.TemporaryDirectory(prefix="release-feed-") as directory:
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
            self.assertIn("release feed pass", result.stdout)


if __name__ == "__main__":
    unittest.main()
