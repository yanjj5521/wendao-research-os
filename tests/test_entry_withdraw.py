from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class EntryWithdrawFlowTests(unittest.TestCase):
    """M1 撤回机制：自撤回、快照、日志、越界确认与恢复。"""

    def test_withdraw_flow(self) -> None:
        script = r'''
import re
import sqlite3
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
# 条目从列表消失，详情 404
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

# 4) 越界（>24h）→ pending_review，需人工确认
old_id = create_entry("越界撤回条目")
with connect() as conn:
    old = (datetime.now().astimezone() - timedelta(hours=30)).isoformat(timespec="seconds")
    conn.execute("UPDATE entries SET created_at=?, updated_at=? WHERE id=?", (old, old, old_id))
r = c.post(f"/entry/{old_id}/withdraw", data={"error_type": "content_error", "description": "结论写错了需要撤回重写"})
assert "待确认" in flash(r.text), flash(r.text)
page = c.get("/library").text
assert "待确认" in page and "确认执行" in page and "取消" in page
with connect() as conn:
    assert conn.execute("SELECT status FROM entries WHERE id=?", (old_id,)).fetchone()[0] == "active"
    wid = conn.execute("SELECT id FROM upload_withdrawals WHERE entry_id=?", (old_id,)).fetchone()[0]

# 5) 取消 → 状态 cancelled
r = c.post(f"/withdrawal/{wid}/cancel")
assert "已取消" in flash(r.text)
# 再次提交越界撤回并确认执行
r = c.post(f"/entry/{old_id}/withdraw", data={"error_type": "content_error", "description": "结论写错了需要撤回重写"})
assert "待确认" in flash(r.text)
with connect() as conn:
    wid2 = conn.execute("SELECT id FROM upload_withdrawals WHERE entry_id=? AND status='pending_review'", (old_id,)).fetchone()[0]
r = c.post(f"/withdrawal/{wid2}/confirm")
assert "已执行" in flash(r.text), flash(r.text)
assert c.get(f"/entry/{old_id}").status_code == 404
with connect() as conn:
    assert conn.execute("SELECT decided_by FROM upload_withdrawals WHERE id=?", (wid2,)).fetchone()[0] == "owner-confirm"

# 6) 参数校验：说明太短、错误类型非法
short_id = create_entry("参数校验条目")
r = c.post(f"/entry/{short_id}/withdraw", data={"error_type": "other", "description": "太短"})
assert "至少 10 个字" in flash(r.text)
r = c.post(f"/entry/{short_id}/withdraw", data={"error_type": "hacker", "description": "类型不合法的撤回请求示例"})
assert "有效的错误类型" in flash(r.text)
print("withdraw flow pass")
'''
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
