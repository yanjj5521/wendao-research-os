from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path

from fastapi import Request


ROOT = Path(__file__).resolve().parents[1]


class FiveFixesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import warnings

        os.environ["RESEARCH_OS_DATA_DIR"] = tempfile.mkdtemp(prefix="five-tests-")
        warnings.filterwarnings("ignore")
        from fastapi.testclient import TestClient

        from web.app import app  # noqa: F401  (mounts hub + inits DBs)

        cls.client = TestClient(app, raise_server_exceptions=False)

    def _flash(self, page: str) -> str:
        match = re.search(r'<div class="flash [^"]+">([^<]*)</div>', page)
        return match.group(1) if match else ""

    # ---- 1. 只保存 ----
    def test_only_save_persists_and_reports_success(self) -> None:
        self.client.get("/online")
        response = self.client.post(
            "/online/settings",
            data={
                "sync_provider": "legacy_hub",
                "hub_url": "http://192.168.1.20:5050",
                "hub_api_token": "tok-123",
                "auto_sync": "1",
                "connect_now": "0",
            },
        )
        self.assertIn("已保存（未联机）", response.text)
        from core.database import get_setting

        self.assertEqual(get_setting("hub_url", ""), "http://192.168.1.20:5050")
        self.assertEqual(get_setting("hub_api_token", ""), "tok-123")
        self.assertEqual(get_setting("sync_provider", ""), "legacy_hub")

    def test_only_save_disabled_reports_saved(self) -> None:
        self.client.get("/online")
        response = self.client.post(
            "/online/settings",
            data={
                "sync_provider": "disabled",
                "hub_url": "http://192.168.1.20:5050",
                "hub_api_token": "",
                "auto_sync": "",
                "connect_now": "0",
            },
        )
        self.assertIn("已保存：联机保持关闭", response.text)

    # ---- 2. 管理员建立段落已删除 ----
    def test_admin_howto_section_removed(self) -> None:
        template = (ROOT / "templates" / "online.html").read_text(encoding="utf-8")
        self.assertNotIn("管理员是怎样建立的", template)
        self.assertNotIn("admin-explainer", template)
        css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
        self.assertNotIn("admin-setup-steps", css)

    # ---- 3. 设置页卡片化 + 5b 模型共享 ----
    def test_settings_cards_render(self) -> None:
        page = self.client.get("/settings").text
        self.assertIn("settings-card-head", page)
        self.assertIn("模型共享与导入", page)

    def test_ai_key_saved_and_share_flow(self) -> None:
        import json

        from core.database import get_setting, set_setting

        set_setting("ai_mode", "offline")
        self.client.post("/settings", data={"ai_api_key": "sk-demo"})  # minimal post只改密钥，其余保持默认
        # 保存后再切到外部接口，验证 share 页会带上已存 key
        set_setting("ai_mode", "openai")
        set_setting("ai_endpoint", "https://api.example.com/v1/responses")
        set_setting("ai_model", "demo-model")
        response = self.client.post("/settings/ai/share", data={"include_key": "1"})
        self.assertIn("含访问密钥", response.text, self._flash(response.text) or response.text[:300])
        self.assertIn("sk-demo", response.text, self._flash(response.text) or response.text[:300])
        import html as _html

        match = re.search(r"<textarea[^>]*>(\{[\s\S]*?\})</textarea>", response.text)
        payload = json.loads(_html.unescape(match.group(1)))
        self.assertEqual(payload["kind"], "research-os-model-share")
        self.assertEqual(payload["api_key"], "sk-demo")

        imported = self.client.post(
            "/settings/ai/share/import", data={"share_code": json.dumps(payload)}
        )
        self.assertIn("已导入共享模型", imported.text)
        page = self.client.get("/settings").text
        profile_id = re.search(r"/settings/ai/profiles/([0-9a-f]{8})/activate", page).group(1)
        activated = self.client.post(f"/settings/ai/profiles/{profile_id}/activate")
        self.assertIn("已启用共享模型", activated.text)
        self.assertEqual(get_setting("ai_api_key", ""), "sk-demo")
        deleted = self.client.post(f"/settings/ai/profiles/{profile_id}/delete")
        self.assertIn("已删除", deleted.text)

    def test_offline_share_is_rejected(self) -> None:
        from core.database import set_setting

        set_setting("ai_mode", "offline")
        response = self.client.post("/settings/ai/share", data={})
        self.assertIn("没有可共享的模型配置", response.text)

    # ---- 4. 注册草稿保留 ----
    def test_register_failure_keeps_entered_fields(self) -> None:
        import tempfile

        data_dir = tempfile.mkdtemp(prefix="five-reg-")
        script = r'''
import os, re
os.environ["RESEARCH_OS_DATA_DIR"] = os.environ["FIVE_REG_DIR"]
from fastapi.testclient import TestClient
from hub.app import app
from hub.db import HUB_ADMIN_PATH, connect_hub, init_hub_db
init_hub_db()
password = re.search(r"密码: (.+)", HUB_ADMIN_PATH.read_text(encoding="utf-8")).group(1).strip()
def csrf(client, path):
    return re.search(r'name="_csrf" value="([^"]+)"', client.get(path).text).group(1)
admin = TestClient(app)
admin.post("/login", data={"username": "admin", "password": password, "_csrf": csrf(admin, "/")})
admin.post("/admin/invites", data={"uses": 3, "days": 7, "_csrf": csrf(admin, "/admin")})
with connect_hub() as conn:
    code = conn.execute("SELECT code FROM hub_invites ORDER BY rowid DESC LIMIT 1").fetchone()["code"]
member = TestClient(app)
member.get("/register")
token = csrf(member, "/register")
first = member.post("/register", data={
    "invite": code, "username": "tom", "display_name": "汤姆", "email": "tom@x.com",
    "password": "Passw0rd!", "password_confirm": "Passw0rd!", "_csrf": token,
})
assert first.url.path == "/me", first.text[:400]
token = csrf(member, "/register")  # 成功后会话已重建，需要取新 CSRF
dup = member.post("/register", data={
    "invite": code, "username": "tom", "display_name": "汤姆", "email": "tom@x.com",
    "password": "Passw0rd!", "password_confirm": "Passw0rd!", "_csrf": token,
})
assert "已被使用" in dup.text, dup.text[:400]
assert 'value="tom"' in dup.text and "汤姆" in dup.text and "tom@x.com" in dup.text, (dup.url, dup.text[:400])
mismatch = member.post("/register", data={
    "invite": code, "username": "tom", "display_name": "汤姆", "email": "tom@x.com",
    "password": "Passw0rd!", "password_confirm": "diff", "_csrf": token,
})
assert "两次密码不一致" in mismatch.text and 'value="tom"' in mismatch.text, mismatch.text[:400]
print("register draft ok")
'''
        env = os.environ.copy()
        env["FIVE_REG_DIR"] = data_dir
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120
        )
        self.assertEqual(result.returncode, 0, result.stderr[-6000:] + "\nSTDOUT: " + result.stdout[-1500:])

    # ---- 5a. 全局错误页 ----
    def test_error_pages_offer_return_home(self) -> None:
        response = self.client.get("/definitely-not-a-page")
        self.assertEqual(response.status_code, 404)
        self.assertIn("返回主界面", response.text)
        self.assertIn("重试当前页面", response.text)
        api = self.client.get("/api/definitely-not-a-page")
        self.assertEqual(api.status_code, 404)
        self.assertIn("Not Found", api.text)

    def test_unhandled_exception_renders_home_button(self) -> None:
        from web import runtime

        def boom(request: Request):
            raise RuntimeError("boom-test")

        runtime.app.add_api_route("/__five_boom__", boom, methods=["GET"])
        response = self.client.get("/__five_boom__")
        self.assertEqual(response.status_code, 500)
        self.assertIn("返回主界面", response.text)
        self.assertIn("__five_boom__", response.text)


if __name__ == "__main__":
    unittest.main()
