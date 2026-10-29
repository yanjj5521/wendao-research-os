from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TeamSocialFlowTests(unittest.TestCase):
    def test_forum_chat_red_packet_and_atomic_trade(self) -> None:
        script = r'''
import re
from fastapi.testclient import TestClient
from hub.app import app
from hub.db import HUB_ADMIN_PATH, balances, connect_hub, create_user, init_hub_db

init_hub_db()
password = re.search(r"密码: (.+)", HUB_ADMIN_PATH.read_text(encoding="utf-8")).group(1).strip()
with connect_hub() as conn:
    member_id, member_token = create_user(conn, "member", "member-password-123", "成员甲")
    conn.commit()

def csrf(client, path):
    page = client.get(path)
    value = re.search(r'name="_csrf" value="([^"]+)"', page.text)
    assert value, page.text[:300]
    return value.group(1)

admin = TestClient(app)
admin.post("/login", data={"username": "admin", "password": password, "_csrf": csrf(admin, "/")})
assert "管理控制台" in admin.get("/admin").text
assert "同行论坛" in admin.get("/forum").text
assert "同行聊天" in admin.get("/chat").text
assert "同行坊市" in admin.get("/market").text
admin.post("/forum", data={"title": "一个可讨论的问题", "body": "请大家用证据回复。", "_csrf": csrf(admin, "/forum")})
admin.post("/chat", data={"body": "今晚一起读图。", "_csrf": csrf(admin, "/chat")})
admin.post("/market/red-packets", data={"recipient_id": member_id, "asset_key": "cultivation", "amount": 10, "note": "加油", "_csrf": csrf(admin, "/market")})
admin.post("/market/trades", data={"recipient_id": member_id, "offer_asset_key": "spirit_stone", "offer_amount": 2, "request_asset_key": "spirit_wood", "request_amount": 1, "_csrf": csrf(admin, "/market")})
with connect_hub() as conn:
    trade_id = conn.execute("SELECT id FROM hub_social_transfers WHERE kind='trade' ORDER BY id DESC LIMIT 1").fetchone()["id"]

member = TestClient(app)
member.post("/login", data={"username": "member", "password": "member-password-123", "_csrf": csrf(member, "/")})
profile = member.post(
    "/me/profile",
    data={
        "display_name": "成员甲",
        "title": "验证者",
        "bio": "让证据说话",
        "skills": "测试",
        "capabilities": "复现",
        "goals": "完成实验",
        "avatar_symbol": "证",
        "public": "1",
        "_csrf": csrf(member, "/me"),
    },
)
assert profile.status_code == 200, profile.text
theme = member.post(
    "/me/theme",
    data={"accent": "sage", "density": "compact", "scene": "paper", "_csrf": csrf(member, "/me")},
)
assert theme.status_code == 200, theme.text
api_theme = member.post(
    "/api/v1/personalization",
    headers={"Authorization": f"Bearer {member_token}"},
    json={"accent": "ink", "density": "focus", "scene": "night"},
)
assert api_theme.status_code == 200, api_theme.text
api_profile = member.post(
    "/api/v1/events",
    headers={"Authorization": f"Bearer {member_token}"},
    json={
        "events": [
            {
                "event_uuid": "profile-update-1",
                "event_type": "profile_updated",
                "payload": {"display_name": "成员甲", "title": "同步验证者", "bio": "已同步"},
            },
            {
                "event_uuid": "theme-update-1",
                "event_type": "personalization_updated",
                "payload": {"accent": "amber", "density": "comfortable", "scene": "warm"},
            },
        ]
    },
)
assert api_profile.status_code == 200, api_profile.text
with connect_hub() as conn:
    stored_profile = conn.execute(
        "SELECT title,theme_json FROM account_profiles WHERE account_id=?", (member_id,)
    ).fetchone()
assert stored_profile["title"] == "同步验证者"
assert '"accent": "amber"' in stored_profile["theme_json"]
unknown = member.post(
    "/api/v1/events",
    headers={"Authorization": f"Bearer {member_token}"},
    json={"events": [{"event_uuid": "unknown-event-1", "event_type": "unknown_event", "payload": {"x": "y"}}]},
)
assert unknown.status_code == 200, unknown.text
assert unknown.json()["results"][0]["status"] == "rejected", unknown.text
oversized = member.post(
    "/api/v1/events",
    headers={
        "Authorization": f"Bearer {member_token}",
        "Content-Type": "application/json",
    },
    content=b"x" * (256 * 1024 + 1),
)
assert oversized.status_code == 413, oversized.text
with connect_hub() as conn:
    assert conn.execute("SELECT 1 FROM hub_sync_events WHERE event_uuid='unknown-event-1'").fetchone() is None
member.post(f"/market/trades/{trade_id}/accept", data={"_csrf": csrf(member, "/market")})
with connect_hub() as conn:
    admin_assets = balances(conn, 1)
    member_assets = balances(conn, member_id)
    status = conn.execute("SELECT status FROM hub_social_transfers WHERE id=?", (trade_id,)).fetchone()["status"]
assert status == "completed"
assert (admin_assets["cultivation"], member_assets["cultivation"]) == (90, 110)
assert (admin_assets["spirit_stone"], member_assets["spirit_stone"]) == (10, 14)
assert (admin_assets["spirit_wood"], member_assets["spirit_wood"]) == (5, 3)
print("team social flow pass")
'''
        with tempfile.TemporaryDirectory(prefix="wendao-team-social-") as data_dir:
            environment = dict(os.environ)
            environment["RESEARCH_OS_DATA_DIR"] = data_dir
            environment["PYTHONPATH"] = str(ROOT)
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=True,
            )
        self.assertIn("team social flow pass", result.stdout)


if __name__ == "__main__":
    unittest.main()
