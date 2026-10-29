from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_hub(url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise RuntimeError(f"ResearchHub stopped before becoming ready.\n{output}")
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("ResearchHub did not become ready within 20 seconds.")


def _create_member(hub_data: Path) -> str:
    env = os.environ.copy()
    env["RESEARCH_OS_DATA_DIR"] = str(hub_data)
    code = """
from hub.db import create_user, hub_transaction, init_hub_db
init_hub_db()
with hub_transaction() as conn:
    _, token = create_user(conn, "sync_self_test", "self-test-password", "联机自检修士")
print(token)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    token = result.stdout.strip().splitlines()[-1]
    if not token:
        raise RuntimeError("Could not create a temporary ResearchHub member.")
    return token


def _exercise_personal_node(personal_data: Path, hub_url: str, token: str) -> None:
    env = os.environ.copy()
    env.update(
        {
            "RESEARCH_OS_DATA_DIR": str(personal_data),
            "RESEARCH_OS_TEST_HUB_URL": hub_url,
            "RESEARCH_OS_TEST_HUB_TOKEN": token,
        }
    )
    code = """
import json
import os
from contextlib import closing
from fastapi.testclient import TestClient
import web.app as app
from web.runtime import navigation_layout
from core.database import connect, get_setting

client = TestClient(app.app)
response = client.post(
    "/online/settings",
    data={
        "sync_provider": "legacy_hub",
        "hub_url": os.environ["RESEARCH_OS_TEST_HUB_URL"],
        "hub_api_token": os.environ["RESEARCH_OS_TEST_HUB_TOKEN"],
        "auto_sync": "1",
        "connect_now": "1",
    },
)
assert response.status_code == 200
assert "联机通道已开启" in response.text
assert get_setting("sync_provider") == "legacy_hub"
assert get_setting("hub_auto_sync") == "1"
with closing(connect()) as conn:
    pending = conn.execute(
        "SELECT COUNT(*) n FROM online_sync_queue WHERE status!='synced'"
    ).fetchone()["n"]
    initial_types = {
        row["event_type"]
        for row in conn.execute("SELECT event_type FROM online_sync_queue")
    }
assert pending == 0
assert {"initial_state_claim", "profile_updated", "personalization_updated"}.issubset(initial_types)

layout = navigation_layout()
groups = {group["key"]: group for group in layout}
layout = [
    groups["system"],
    groups["cultivation"],
    groups["knowledge"],
    groups["workspaces"],
    groups["growth"],
]
for item in groups["system"]["items"]:
    if item["key"] == "assistant":
        item["visible"] = False
settings_response = client.post(
    "/settings",
    data={
        "site_name": "问道科研",
        "researcher_name": "修士",
        "domains": "电化学\\n未分类",
        "ai_mode": "offline",
        "ai_endpoint": "http://127.0.0.1:11434/api/generate",
        "ai_model": "qwen2.5:7b",
        "realm_names": "",
        "nav_labels": "",
        "nav_layout": json.dumps(layout, ensure_ascii=False),
        "review_popup": "1",
        "poem_pool": "",
    },
)
assert settings_response.status_code == 200
assert json.loads(get_setting("nav_layout", "[]"))[0]["key"] == "system"
with closing(connect()) as conn:
    assert conn.execute(
        "SELECT COUNT(*) n FROM online_sync_queue WHERE status!='synced'"
    ).fetchone()["n"] == 0

purchase = client.post("/world/artifacts/qingxin_slip/buy")
assert purchase.status_code == 200
assert "已获得法器" in purchase.text
with closing(connect()) as conn:
    artifact = conn.execute(
        "SELECT level FROM inventory_items WHERE item_key='qingxin_slip'"
    ).fetchone()
    stones = conn.execute(
        "SELECT COALESCE(SUM(amount),0) n FROM asset_transactions "
        "WHERE asset_key='spirit_stone'"
    ).fetchone()["n"]
    pending = conn.execute(
        "SELECT COUNT(*) n FROM online_sync_queue WHERE status!='synced'"
    ).fetchone()["n"]
assert artifact and int(artifact["level"]) == 1
assert int(stones) == 0
assert pending == 0

repeat = client.post("/world/artifacts/qingxin_slip/buy")
assert repeat.status_code == 200
with closing(connect()) as conn:
    assert conn.execute(
        "SELECT COUNT(*) n FROM inventory_items WHERE item_key='qingxin_slip'"
    ).fetchone()["n"] == 1
client.close()
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )
    if result.returncode:
        raise RuntimeError(
            "Personal-node online integration subprocess failed.\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


def _verify_hub(hub_data: Path) -> None:
    env = os.environ.copy()
    env["RESEARCH_OS_DATA_DIR"] = str(hub_data)
    code = """
import json
from contextlib import closing
from hub.db import balances, connect_hub
with closing(connect_hub()) as conn:
    user = conn.execute(
        "SELECT id,display_name FROM accounts WHERE username='sync_self_test'"
    ).fetchone()
    assert user and user["display_name"] == "修士"
    event_types = [
        row["event_type"]
        for row in conn.execute(
            "SELECT event_type FROM hub_sync_events WHERE user_id=? ORDER BY id",
            (user["id"],),
        )
    ]
    assert event_types.count("initial_state_claim") == 1
    assert event_types.count("artifact_buy") == 1
    assert {"profile_updated", "personalization_updated"}.issubset(event_types)
    inventory = conn.execute(
        "SELECT level FROM hub_inventory WHERE user_id=? AND item_key='qingxin_slip'",
        (user["id"],),
    ).fetchone()
    assert inventory and int(inventory["level"]) == 1
    assert balances(conn, int(user["id"]))["spirit_stone"] == 0
    theme = json.loads(
        conn.execute(
            "SELECT theme_json FROM account_profiles WHERE user_id=?",
            (user["id"],),
        ).fetchone()["theme_json"]
    )
    assert theme["nav_layout"][0]["key"] == "system"
    assert {
        item["key"]: item["visible"]
        for item in theme["nav_layout"][0]["items"]
    }["assistant"] is False
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError(
            "ResearchHub verification subprocess failed.\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


def main() -> None:
    port = _free_port()
    hub_url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="research-os-online-self-test-") as root:
        test_root = Path(root)
        hub_data = test_root / "hub"
        personal_data = test_root / "personal"
        token = _create_member(hub_data)
        env = os.environ.copy()
        env["RESEARCH_OS_DATA_DIR"] = str(hub_data)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "hub.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            _wait_for_hub(hub_url, process)
            _exercise_personal_node(personal_data, hub_url, token)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        _verify_hub(hub_data)
    print("ONLINE INTEGRATION TEST PASS")
    print("Save-and-connect, initial merge, artifact transaction and automatic sync are ready.")


if __name__ == "__main__":
    main()
