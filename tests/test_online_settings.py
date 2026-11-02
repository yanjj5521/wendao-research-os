from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class OnlineSettingsSaveFlow(unittest.TestCase):
    """只保存与校验失败的暂存行为（子进程隔离）。"""

    def test_flow(self) -> None:
        script = r'''
import re
from fastapi.testclient import TestClient
from core.database import connect, get_setting, init_db
init_db()
c = TestClient(__import__("web.app", fromlist=["app"]).app)

def flash(text):
    m = re.search(r'<div class="flash [^"]+">([^<]*)</div>', text)
    return m.group(1) if m else ""

def db(key):
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else ""

r = c.post("/online/settings", data={"sync_provider": "legacy_hub", "hub_url": "http://192.168.1.30:5050",
                                     "hub_api_token": "", "auto_sync": "", "connect_now": "1"})
msg = flash(r.text)
assert "必须填写 Token" in msg and "已暂存" in msg, msg
assert db("hub_url") == "http://192.168.1.30:5050"

r = c.post("/online/settings", data={"sync_provider": "legacy_hub", "hub_url": "http://192.168.1.20:5050",
                                     "hub_api_token": "tok-123", "auto_sync": "1", "connect_now": "0"})
assert flash(r.text) == "已保存（未联机）：中心地址与 Token 已写入本机数据库；需要同步时点击“保存并联机”或“立即同步”。", flash(r.text)
for key, value in {"sync_provider": "legacy_hub", "hub_url": "http://192.168.1.20:5050",
                   "hub_api_token": "tok-123", "hub_auto_sync": "1"}.items():
    assert db(key) == value, (key, db(key))
page = c.get("/online").text
assert "http://192.168.1.20:5050" in page and "已安全保存" in page
assert "启动联机中心" in page and "/online/hub-host/start" in page
assert "进入同行会" in page
assert "Headscale 虚拟专网" in page
assert "作为中心启动" in page and "作为成员连接" in page

r = c.post("/online/headnet/save", data={
    "control_url": "https://headscale.example.com",
    "auth_key": "test-preauth-key",
    "hostname": "wendao-test",
    "peer": "100.64.0.10",
    "port": "5050",
    "peer_port": "5050",
})
assert "Headscale 控制端配置已保存" in flash(r.text), flash(r.text)
page = c.get("/online").text
assert "https://headscale.example.com" in page
assert "已安全保存" in page

# Tailscale/WireGuard-style CGNAT addresses are private enough for HTTP.
r = c.post("/online/settings", data={"sync_provider": "legacy_hub", "hub_url": "http://100.64.0.10:5050",
                                     "hub_api_token": "tok-vpn", "auto_sync": "", "connect_now": "0"})
assert "已保存（未联机）" in flash(r.text), flash(r.text)
assert db("hub_url") == "http://100.64.0.10:5050"
assert "http://100.64.0.10:5050" in c.get("/online").text

r = c.post("/online/settings", data={"sync_provider": "disabled", "hub_url": "", "hub_api_token": "",
                                     "auto_sync": "", "connect_now": "0"})
assert "已保存：联机保持关闭" in flash(r.text)
assert db("sync_provider") == "disabled"

r = c.post("/online/settings", data={"sync_provider": "legacy_hub", "hub_url": "ftp://not-supported",
                                     "hub_api_token": "tok-1", "auto_sync": "", "connect_now": "0"})
msg = flash(r.text)
assert "已暂存" in msg, msg
assert db("hub_url") == "ftp://not-supported" and db("sync_provider") == "legacy_hub"

r = c.post("/online/settings", data={"sync_provider": "legacy_hub", "hub_url": "http://192.168.1.20:5050",
                                     "hub_api_token": "first-token", "auto_sync": "", "connect_now": "0"})
r = c.post("/online/settings", data={"sync_provider": "legacy_hub", "hub_url": "http://192.168.1.40:5050",
                                     "hub_api_token": "", "auto_sync": "", "connect_now": "0"})
assert db("hub_api_token") == "first-token" and db("hub_url") == "http://192.168.1.40:5050"
print("online save-only flow pass")

'''
        with tempfile.TemporaryDirectory(prefix="OnlineSettingsSaveFlow-") as directory:
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
