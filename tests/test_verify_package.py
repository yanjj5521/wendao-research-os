from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class VerifyPackageTests(unittest.TestCase):
    """手动下载的更新包完整性校验（/online/verify-package）。"""

    def test_verify_package_flow(self) -> None:
        script = r'''
import hashlib
import io
import re
import zipfile
from fastapi.testclient import TestClient
from web.app import app
from core.database import init_db
init_db()
c = TestClient(app)

# 页面上有校验入口
page = c.get("/online").text
assert "校验文件完整性" in page and "/online/verify-package" in page

# 造一个小 zip 作为待校验文件
buffer = io.BytesIO()
with zipfile.ZipFile(buffer, "w") as archive:
    archive.writestr("WendaoResearchV3.exe", b"MZ fake")
payload = buffer.getvalue()
digest = hashlib.sha256(payload).hexdigest()

# 1) 粘贴正确哈希 → 一致
r = c.post("/online/verify-package",
           files={"package": ("WendaoResearch-v1.8.3-User.zip", payload, "application/zip")},
           data={"expected": digest})
assert "一致，文件完整" in r.text, r.text[:300]

# 2) 粘贴 SHA256SUMS 整行 → 也能识别
r = c.post("/online/verify-package",
           files={"package": ("WendaoResearch-v1.8.3-User.zip", payload, "application/zip")},
           data={"expected": f"{digest}  WendaoResearch-v1.8.3-User.zip"})
assert "一致，文件完整" in r.text

# 3) 粘贴错误哈希 → 明确拒绝并提示不要替换 EXE
r = c.post("/online/verify-package",
           files={"package": ("broken.zip", payload, "application/zip")},
           data={"expected": "0" * 64})
assert "不一致" in r.text and "请勿替换 EXE" in r.text

# 4) 不填预期 → 只给出计算值供人工比对
r = c.post("/online/verify-package",
           files={"package": ("pkg.zip", payload, "application/zip")},
           data={"expected": ""})
assert "SHA256SUMS.txt" in r.text and digest in r.text
print("verify package flow pass")
'''
        with tempfile.TemporaryDirectory(prefix="verify-pkg-") as directory:
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
