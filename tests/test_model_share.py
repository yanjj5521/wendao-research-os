from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ModelShareAndErrorPage(unittest.TestCase):
    """模型共享码全流程 + 全局错误页返回主界面（子进程隔离）。"""

    def test_flow(self) -> None:
        script = r'''
import json
import re
from fastapi.testclient import TestClient
from core.database import get_setting, init_db, set_setting
from services.ai_provider import (activate_profile, export_share_code,
                                  import_share_code, list_profiles)
init_db()
c = TestClient(__import__("web.app", fromlist=["app"]).app)

def configure(key="sk-test-123"):
    set_setting("ai_mode", "openai")
    set_setting("ai_endpoint", "http://192.168.1.5:8000/v1")
    set_setting("ai_model", "qwen2.5-14b")
    set_setting("ai_api_key", key)

try:
    export_share_code()
    raise SystemExit("offline export should fail")
except ValueError:
    pass

configure()
code = export_share_code(include_key=False)
payload = json.loads(code)
assert payload["kind"] == "research-os-model-share" and "api_key" not in payload
code_key = export_share_code(include_key=True)
assert json.loads(code_key)["api_key"] == "sk-test-123"
assert "包含访问口令" in json.loads(code_key)["note"]

for bad in ("", "not json", json.dumps({"kind": "other"})):
    try:
        import_share_code(bad)
        raise SystemExit("bad payload should fail")
    except ValueError:
        pass

profile = import_share_code(code)
assert profile["origin"] == "imported"
assert len(list_profiles()) == 1
activate_profile(profile["id"])
assert get_setting("ai_mode") == "openai"
assert get_setting("ai_endpoint") == "http://192.168.1.5:8000/v1"
assert get_setting("ai_model") == "qwen2.5-14b"

page = c.post("/settings/ai/share", data={"include_key": "0"}).text
assert "模型共享码" in page and "research-os-model-share" in page
assert "仅限其个人使用" in page
imported = c.post("/settings/ai/share/import", data={"share_code": code})
assert "已导入共享模型" in imported.text, imported.text[:300]
settings_page = c.get("/settings").text
assert "模型共享与导入" in settings_page and "仅本人使用" in settings_page

r404 = c.get("/definitely-not-a-page")
assert r404.status_code == 404
assert "返回主界面" in r404.text and 'href="/"' in r404.text
assert "/definitely-not-a-page" in r404.text
print("model share and error page pass")

'''
        with tempfile.TemporaryDirectory(prefix="ModelShareAndErrorPage-") as directory:
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
