from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class GroupPacketAndChatTests(unittest.TestCase):
    def test_group_packet_split_claim_expiry_and_chat_json(self) -> None:
        script = r'''
import re
from fastapi.testclient import TestClient
from hub.app import app
from hub.db import connect_hub, create_user, init_hub_db

init_hub_db()
with connect_hub() as conn:
    ids = {}
    for name in ("alice", "bob", "carol", "dave"):
        ids[name], _ = create_user(conn, name, "Passw0rd!", name.title())
    conn.commit()

def csrf(client, path):
    page = client.get(path)
    value = re.search(r'name="_csrf" value="([^"]+)"', page.text)
    assert value, page.text[:300]
    return value.group(1)

def login(name):
    client = TestClient(app)
    client.post(
        "/login",
        data={"username": name, "password": "Passw0rd!", "_csrf": csrf(client, "/")},
    )
    return client

alice, bob, carol, dave = (login(name) for name in ("alice", "bob", "carol", "dave"))
create = alice.post(
    "/market/group-red-packets",
    data={
        "asset_key": "spirit_stone",
        "total_amount": 10,
        "shares": 3,
        "note": "群红包回归",
        "_csrf": csrf(alice, "/market"),
    },
)
assert create.status_code == 200
with connect_hub() as conn:
    packet = conn.execute("SELECT * FROM hub_red_packets ORDER BY id DESC LIMIT 1").fetchone()
    packet_id = int(packet["id"])
self_claim = alice.post(
    f"/market/group-red-packets/{packet_id}/claim",
    data={"_csrf": csrf(alice, "/market")},
)
assert self_claim.status_code == 200
assert "抢到" in self_claim.text
for client in (bob, carol):
    response = client.post(
        f"/market/group-red-packets/{packet_id}/claim",
        data={"_csrf": csrf(client, "/market")},
    )
    assert response.status_code == 200
exhausted = dave.post(
    f"/market/group-red-packets/{packet_id}/claim",
    data={"_csrf": csrf(dave, "/market")},
)
assert exhausted.status_code == 200
assert "已抢完" in exhausted.text
with connect_hub() as conn:
    packet = conn.execute("SELECT * FROM hub_red_packets WHERE id=?", (packet_id,)).fetchone()
    claims = [
        int(row["amount"])
        for row in conn.execute(
            "SELECT amount FROM hub_red_packet_claims WHERE packet_id=?", (packet_id,)
        )
    ]
assert packet["status"] == "finished"
assert int(packet["shares_left"]) == 0
assert sum(claims) == 10
assert all(amount >= 1 for amount in claims)
assert "已抢到" in bob.get("/market").text

# chat: JSON send returns immediately and incremental polling sees it once
sent = alice.post(
    "/chat/send.json",
    data={"_csrf": csrf(alice, "/chat"), "body": "即时消息回归"},
)
payload = sent.json()
assert sent.status_code == 200 and payload["ok"]
message_id = int(payload["message"]["id"])
feed = bob.get(f"/chat/messages.json?after={message_id - 1}").json()
assert any(item["body"] == "即时消息回归" for item in feed["messages"])
empty = bob.get(f"/chat/messages.json?after={message_id}").json()
assert empty["messages"] == []
assert 'data-priority="chat-immediate"' in alice.get("/chat").text

# expiry: an unclaimed packet is refunded when the market is opened
alice.post(
    "/market/group-red-packets",
    data={
        "asset_key": "spirit_stone",
        "total_amount": 2,
        "shares": 2,
        "note": "过期退款回归",
        "_csrf": csrf(alice, "/market"),
    },
)
with connect_hub() as conn:
    packet_id = int(
        conn.execute("SELECT id FROM hub_red_packets ORDER BY id DESC LIMIT 1").fetchone()["id"]
    )
    before = conn.execute(
        "SELECT COALESCE(SUM(amount),0) n FROM hub_asset_transactions WHERE user_id=? AND asset_key='spirit_stone'",
        (ids["alice"],),
    ).fetchone()["n"]
    conn.execute(
        "UPDATE hub_red_packets SET expires_at='2020-01-01T00:00:00+08:00' WHERE id=?",
        (packet_id,),
    )
    conn.commit()
alice.get("/market")
with connect_hub() as conn:
    after = conn.execute(
        "SELECT COALESCE(SUM(amount),0) n FROM hub_asset_transactions WHERE user_id=? AND asset_key='spirit_stone'",
        (ids["alice"],),
    ).fetchone()["n"]
    status = conn.execute(
        "SELECT status FROM hub_red_packets WHERE id=?", (packet_id,)
    ).fetchone()["status"]
assert after - before == 2
assert status == "expired"
print("group packet and chat flow pass")
'''
        with tempfile.TemporaryDirectory(prefix="hub-group-") as directory:
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
