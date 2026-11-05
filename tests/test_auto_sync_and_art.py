from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AutoSyncAndArtTests(unittest.TestCase):
    """境界自动同步的节流逻辑 + 插图资源可用。"""

    def test_auto_realm_throttle(self) -> None:
        script = r'''
from core.database import init_db, set_setting, get_setting
from services import hub_realm

init_db()
pushed: list[dict] = []

def fake_push(url, token):
    pushed.append({"url": url, "token": token, **hub_realm.snapshot()})
    return {"realm_name": hub_realm.snapshot()["realm_name"]}

hub_realm.push = fake_push

# 没配置中心 → 不推送
assert hub_realm.sync_if_due() is None
assert not pushed

set_setting("hub_url", "http://127.0.0.1:9")
set_setting("hub_api_token", "token-123")
first = hub_realm.sync_if_due()
assert first and len(pushed) == 1, pushed
assert get_setting("hub_realm_signature", ""), "首次同步应记录签名"

# 签名未变且在节流窗口内 → 不重复推送
assert hub_realm.sync_if_due() is None
assert len(pushed) == 1

# 强制推送 → 仍然推送
assert hub_realm.sync_if_due(force=True)
assert len(pushed) == 2
print("throttle pass")
'''
        with tempfile.TemporaryDirectory(prefix="realm-sync-") as directory:
            env = os.environ.copy()
            env["RESEARCH_OS_DATA_DIR"] = directory
            result = subprocess.run(
                [sys.executable, "-c", script], cwd=ROOT, env=env,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("throttle pass", result.stdout)

    def test_art_assets_render(self) -> None:
        script = r'''
from pathlib import Path

from fastapi.testclient import TestClient

from core.database import init_db
from web.app import app

init_db()
client = TestClient(app)

arts = ["alchemy.webp", "forge.webp", "spirit-beast.webp"]
for name in arts:
    path = Path("static/art") / name
    assert path.is_file() and path.stat().st_size > 50_000, (name, path.stat().st_size if path.is_file() else 0)
    response = client.get(f"/static/art/{name}")
    assert response.status_code == 200 and len(response.content) > 50_000

for page in ("/alchemy", "/world", "/pets", "/professions"):
    body = client.get(page).text
    assert "art/" in body and ".webp" in body, page

# 百艺页三张工艺图都要出现
home = client.get("/professions").text
for name in arts:
    assert name in home, name
print("art pass")
'''
        with tempfile.TemporaryDirectory(prefix="art-") as directory:
            env = os.environ.copy()
            env["RESEARCH_OS_DATA_DIR"] = directory
            result = subprocess.run(
                [sys.executable, "-c", script], cwd=ROOT, env=env,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("art pass", result.stdout)


if __name__ == "__main__":
    unittest.main()
