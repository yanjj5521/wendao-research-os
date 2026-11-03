from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class EntryWithdrawFlowTests(unittest.TestCase):
    """M1 撤回机制：自撤回、快照、日志、越界确认与恢复、窗口配置与归档。

    注意：直接 SQL 与 TestClient 请求严格分阶段执行（先写库并关闭连接，
    再发请求），避免测试进程内连接交错持锁。
    """

    def test_withdraw_flow(self) -> None:
        script = r'''
import re
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from web.app import app
from core.database import connect, init_db
init_db()
c = TestClient(app)

def flash(text):
    m = re.search(r'<div class="flash[^"]*">([^<]*)</div>', text)
    return m.group(1) if m else ""

def create_entry(title):
    r = c.post("/notes/new", data={"title": title, "content": "测试内容", "kind": "note"})
    m = re.search(r"/entry/(\d+)", str(r.url))
    assert m, r.text[:300]
    return int(m.group(1))

# 1) 24h 内 → 自动撤回
entry_id = create_entry("撤回演练条目")
r = c.post(f"/entry/{entry_id}/withdraw", data={"error_type": "wrong_file", "description": "传错了实验数据文件，请撤回"})
assert "已自动撤回" in r.text, r.text[:300]
page = c.get("/library").text
assert "撤回记录" in page and "已撤回" in page and "从快照恢复" in page
assert "撤回演练条目" not in page.split("撤回记录")[0]
assert c.get(f"/entry/{entry_id}").status_code == 404
with connect() as conn:
    assert conn.execute("SELECT status FROM entries WHERE id=?", (entry_id,)).fetchone()[0] == "withdrawn"
    rev = conn.execute("SELECT rev_no, snapshot_json FROM entry_revisions WHERE entry_id=?", (entry_id,)).fetchone()
    assert rev and '"title"' in rev[1]
    acts = [x[0] for x in conn.execute("SELECT action FROM activities WHERE entry_id=?", (entry_id,))]
    assert "withdraw" in acts

# 2) 重复撤回被拒绝
r = c.post(f"/entry/{entry_id}/withdraw", data={"error_type": "other", "description": "再次撤回的请求应当被拒绝"})
assert "已经撤回" in flash(r.text), flash(r.text)

# 3) 快照恢复
r = c.post(f"/entry/{entry_id}/restore")
assert "已从撤回快照恢复" in flash(r.text), flash(r.text)
page = c.get("/library").text
assert "撤回演练条目" in page.split("撤回记录")[0]
with connect() as conn:
    assert conn.execute("SELECT status FROM entries WHERE id=?", (entry_id,)).fetchone()[0] == "active"
    acts = [x[0] for x in conn.execute("SELECT action FROM activities WHERE entry_id=?", (entry_id,))]
    assert "withdraw_restore" in acts

# 4) 越界（>72h）→ pending_review，需人工确认
old_id = create_entry("越界撤回条目")
conn = connect()
old = (datetime.now().astimezone() - timedelta(hours=80)).isoformat(timespec="seconds")
conn.execute("UPDATE entries SET created_at=?, updated_at=? WHERE id=?", (old, old, old_id))
conn.commit()
conn.close()
r = c.post(f"/entry/{old_id}/withdraw", data={"error_type": "content_error", "description": "结论写错了需要撤回重写"})
assert "待确认" in flash(r.text), flash(r.text)
page = c.get("/library").text
assert "待确认" in page and "确认执行" in page and "取消" in page
with connect() as conn:
    assert conn.execute("SELECT status FROM entries WHERE id=?", (old_id,)).fetchone()[0] == "active"
    wid = conn.execute("SELECT id FROM upload_withdrawals WHERE entry_id=?", (old_id,)).fetchone()[0]

# 5) 取消 → 状态 cancelled；再次提交并确认执行
r = c.post(f"/withdrawal/{wid}/cancel")
assert "已取消" in flash(r.text)
r = c.post(f"/entry/{old_id}/withdraw", data={"error_type": "content_error", "description": "结论写错了需要撤回重写"})
assert "待确认" in flash(r.text)
with connect() as conn:
    wid2 = conn.execute("SELECT id FROM upload_withdrawals WHERE entry_id=? AND status='pending_review'", (old_id,)).fetchone()[0]
r = c.post(f"/withdrawal/{wid2}/confirm")
assert "已执行" in flash(r.text), flash(r.text)
assert c.get(f"/entry/{old_id}").status_code == 404
with connect() as conn:
    assert conn.execute("SELECT decided_by FROM upload_withdrawals WHERE id=?", (wid2,)).fetchone()[0] == "owner-confirm"

# 6) 参数校验
short_id = create_entry("参数校验条目")
r = c.post(f"/entry/{short_id}/withdraw", data={"error_type": "other", "description": "太短"})
assert "至少 10 个字" in flash(r.text)
r = c.post(f"/entry/{short_id}/withdraw", data={"error_type": "hacker", "description": "类型不合法的撤回请求示例"})
assert "有效的错误类型" in flash(r.text)
print("withdraw flow pass")
'''
        self._run(script)

    def test_window_configurable_and_archive(self) -> None:
        script = r'''
import re
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from core.database import connect, init_db
from services import withdrawal as ws
init_db()

# ---- 阶段一：直接 SQL 准备数据（全部提交并关闭连接） ----
conn = connect()
assert ws.window_hours(conn) == 72
conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('withdraw_window_hours','2')")
conn.commit()
assert ws.window_hours(conn) == 2

now = datetime.now().astimezone()
def insert_entry(title, created_at):
    cur = conn.execute(
        "INSERT INTO entries(title,kind,domain,tags,summary,content,status,created_at,updated_at) "
        "VALUES (?,'note','未分类','','',?,'active',?,?)",
        (title, "内容", created_at, created_at),
    )
    return int(cur.lastrowid)

tight_id = insert_entry("窗口收紧条目", (now - timedelta(hours=5)).isoformat(timespec="seconds"))
old_stamp = (now - timedelta(days=91)).isoformat(timespec="seconds")
keep_id = insert_entry("恢复保护条目", old_stamp)
drop_id = insert_entry("归档演练条目", old_stamp)
conn.execute(
    "INSERT INTO upload_withdrawals(entry_id,error_type,description,status,auto_level,"
    "decided_by,decision_note,created_at,decided_at) VALUES "
    "(?,'wrong_file','保护恢复入口的记录','approved','auto','system','边界内自动执行',?,?)",
    (keep_id, old_stamp, old_stamp),
)
conn.execute("UPDATE entries SET status='withdrawn' WHERE id=?", (keep_id,))
conn.execute(
    "INSERT INTO upload_withdrawals(entry_id,error_type,description,status,auto_level,"
    "decided_by,decision_note,created_at,decided_at) VALUES "
    "(?,'other','应当被归档的老记录','cancelled','auto','owner','',?,?)",
    (drop_id, old_stamp, old_stamp),
)
conn.commit()
conn.close()

# ---- 阶段二：纯 HTTP 请求 ----
c = TestClient(__import__("web.app", fromlist=["app"]).app)

def flash(text):
    m = re.search(r'<div class="flash[^"]*">([^<]*)</div>', text)
    return m.group(1) if m else ""

# 窗口收紧到 2h：5h 前的条目 → 待确认
r = c.post(f"/entry/{tight_id}/withdraw", data={"error_type": "other", "description": "收紧窗口后的越界撤回请求"})
assert "待确认" in flash(r.text), flash(r.text)
with connect() as conn:
    assert conn.execute("SELECT status FROM entries WHERE id=?", (tight_id,)).fetchone()[0] == "active"

# 90 天归档：知识库页加载时惰性归档；恢复入口受保护
page = c.get("/library").text
records = page.split("撤回记录")[-1]
assert "归档演练条目" not in records
assert "恢复保护条目" in records
conn = connect()
assert conn.execute("SELECT status FROM upload_withdrawals WHERE entry_id=?", (drop_id,)).fetchone()[0] == "archived"
assert conn.execute("SELECT status FROM upload_withdrawals WHERE entry_id=?", (keep_id,)).fetchone()[0] == "approved"
conn.close()
print("window and archive pass")
'''
        self._run(script)

    def _run(self, script: str) -> None:
        with tempfile.TemporaryDirectory(prefix="withdraw-") as directory:
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
