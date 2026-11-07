from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ConcurrencyStabilityTests(unittest.TestCase):
    """多用户并发：混合读写不 500、福利额度不被并发超发、设置缓存写穿透。"""

    def test_concurrent_stability(self) -> None:
        script = r'''
import re
import threading

from fastapi.testclient import TestClient

from core.database import (
    clear_settings_cache,
    connect,
    get_setting,
    init_db,
    set_setting,
    total_xp,
)
from hub.app import app as hub_app
from hub.db import HUB_ADMIN_PATH, connect_hub, create_user, hub_transaction, init_hub_db, weekly_quotas

init_db()
init_hub_db()

# ---- 1) 设置缓存：写穿透、并发读写无异常 ----
set_setting("cache-probe", "before")
threads_results: list[str] = []
errors: list[Exception] = []
lock = threading.Lock()

def reader_writer(index: int) -> None:
    try:
        for round_index in range(20):
            value = get_setting("cache-probe", "")
            if value not in {"before", "after"}:
                raise AssertionError(f"读到意外值: {value!r}")
            if index % 4 == 0 and round_index == 0:
                set_setting("cache-probe", "after")
            with lock:
                threads_results.append(value)
    except Exception as exc:  # noqa: BLE001
        with lock:
            errors.append(exc)

threads = [threading.Thread(target=reader_writer, args=(i,)) for i in range(8)]
for t in threads: t.start()
for t in threads: t.join()
assert not errors, errors
assert get_setting("cache-probe") == "after"
clear_settings_cache()

# ---- 2) 个人端：16 线程混合读写，全部 2xx/3xx，笔记数与写请求数一致 ----
client = TestClient(app if False else __import__("web.app", fromlist=["app"]).app)
personal_errors: list[str] = []
note_posts = 0
note_lock = threading.Lock()

def personal_worker(index: int) -> None:
    global note_posts
    local = TestClient(client.app)
    try:
        for round_index in range(8):
            page = local.get("/daily")
            if page.status_code != 200:
                raise AssertionError(f"/daily -> {page.status_code}")
            if index % 2 == 0:
                note = local.post(
                    "/notes/new",
                    data={"title": f"并发笔记 {index}-{round_index}", "kind": "note"},
                    follow_redirects=False,
                )
                if note.status_code != 303:
                    raise AssertionError(f"/notes/new -> {note.status_code}")
                with note_lock:
                    note_posts += 1
    except Exception as exc:  # noqa: BLE001
        with note_lock:
            personal_errors.append(str(exc))

workers = [threading.Thread(target=personal_worker, args=(i,)) for i in range(16)]
for t in workers: t.start()
for t in workers: t.join()
assert not personal_errors, personal_errors[:3]
with connect() as conn:
    created = conn.execute(
        "SELECT COUNT(*) n FROM entries WHERE title LIKE '并发笔记 %'"
    ).fetchone()["n"]
assert created == note_posts, (created, note_posts)
assert total_xp() >= 0

# ---- 3) 中心：两个"管理员"并发给同一成员发满额福利，不允许超发 ----
with hub_transaction() as conn:
    member_id, _ = create_user(conn, "welfarerace", "Passw0rd!123", "并发福利成员")
    quota = weekly_quotas(conn)["cultivation"]
assert quota > 0, quota

password = re.search(r"密码: (.+)", HUB_ADMIN_PATH.read_text(encoding="utf-8")).group(1).strip()
admin_a = TestClient(hub_app, base_url="http://hubserver", root_path="")
admin_b = TestClient(hub_app, base_url="http://hubserver", root_path="")
for cli in (admin_a, admin_b):
    csrf = re.search(r'name="_csrf" value="([^"]+)"', cli.get("/").text).group(1)
    assert cli.post("/login", data={"username": "admin", "password": password, "_csrf": csrf},
                    follow_redirects=False).status_code == 303

payloads = {
    "recipient_id": str(member_id), "cultivation": str(quota),
    "reason": "并发测试", "_csrf": "",
}
results: list[int] = []

def grant(cli: TestClient) -> None:
    csrf = re.search(r'name="_csrf" value="([^"]+)"', cli.get("/admin").text).group(1)
    payloads["_csrf"] = csrf
    response = cli.post("/admin/welfare", data=payloads, follow_redirects=False)
    results.append(response.status_code)

race = [threading.Thread(target=grant, args=(cli,)) for cli in (admin_a, admin_b)]
for t in race: t.start()
for t in race: t.join()
assert all(code in (303, 200) for code in results), results

with connect_hub() as conn:
    granted = conn.execute(
        "SELECT COALESCE(SUM(cultivation),0) n FROM hub_welfare_grants WHERE recipient_id=?",
        (member_id,),
    ).fetchone()["n"]
assert granted <= quota, (granted, quota)  # 额度检查对并发管理员是原子的

# ---- 4) 中心未捕获异常走友好页而不是裸 500 ----
from hub.common import hub_url_for  # noqa: F401  确认上下文可导入
bad = admin_a.get("/api/v1/definitely-not-exist")
assert bad.status_code == 404
print("concurrency pass")
'''
        with tempfile.TemporaryDirectory(prefix="concurrency-") as directory:
            env = os.environ.copy()
            env["RESEARCH_OS_DATA_DIR"] = directory
            result = subprocess.run(
                [sys.executable, "-c", script], cwd=ROOT, env=env,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("concurrency pass", result.stdout)


if __name__ == "__main__":
    unittest.main()
