"""账号检索基准：验证账号量增长后仍走索引。

用法：
    python tools/bench_accounts.py --accounts 100000

只测查询，不测口令哈希——10 万次 PBKDF2 迭代会让基准变成密码学测试，
而不是索引测试。真实注册走 core.accounts.create_account。
"""

from __future__ import annotations

import argparse
import os
import secrets
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def seed(conn: sqlite3.Connection, count: int) -> None:
    now = "2026-10-26T12:00:00+08:00"
    rows = [
        (
            f"user{i:06d}",
            f"研究者{i:06d}",
            f"u{i:06d}@example.com",
            "member",
            "active",
            now,
            now,
        )
        for i in range(count)
    ]
    conn.executemany(
        "INSERT INTO accounts(username,display_name,email,role,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    salt = secrets.token_hex(16)
    ids = [
        row["id"]
        for row in conn.execute(
            "SELECT a.id FROM accounts a LEFT JOIN account_credentials c ON c.account_id=a.id "
            "WHERE c.account_id IS NULL ORDER BY a.id"
        )
    ]
    creds = [(i, salt, "0" * 64, f"token-{i:06d}", now) for i in ids]
    conn.executemany(
        "INSERT INTO account_credentials(account_id,password_salt,password_hash,api_token,updated_at) VALUES (?,?,?,?,?)",
        creds,
    )
    conn.commit()


def timed(label: str, fn) -> None:
    start = time.perf_counter()
    result = fn()
    ms = (time.perf_counter() - start) * 1000
    print(f"  {label:<28} {ms:8.2f} ms   {result}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--accounts", type=int, default=100_000)
    args = parser.parse_args()

    temp_dir = tempfile.mkdtemp(prefix="hub-bench-")
    os.environ["RESEARCH_OS_DATA_DIR"] = temp_dir
    from core.accounts import account_by_token, authenticate, count_accounts, list_accounts, search_accounts
    from hub.db import connect_hub, init_hub_db

    init_hub_db()
    conn = connect_hub()
    print(f"灌入 {args.accounts:,} 个账号 …")
    start = time.perf_counter()
    seed(conn, args.accounts)
    print(f"  写入耗时 {(time.perf_counter() - start):.2f}s\n")

    probe = conn.execute("SELECT id,username FROM accounts ORDER BY id DESC LIMIT 1").fetchone()
    probe_id, probe_name = probe["id"], probe["username"]
    token = f"token-{probe_id:06d}"

    print("查询计划")
    for label, sql in (
        ("按用户名", f"SELECT * FROM accounts WHERE username='{probe_name}'"),
        ("按Token", f"SELECT a.* FROM accounts a JOIN account_credentials c ON c.account_id=a.id WHERE c.api_token='{token}'"),
        ("成员分页", "SELECT * FROM accounts ORDER BY role, created_at DESC, id DESC LIMIT 50 OFFSET 50000"),
        ("状态计数", "SELECT COUNT(*) FROM accounts WHERE status='active'"),
    ):
        plan = conn.execute("EXPLAIN QUERY PLAN " + sql).fetchall()
        print(f"  {label}: {plan[0]['detail']}")

    print("\n实测")
    timed("按用户名（唯一索引）", lambda: conn.execute("SELECT id FROM accounts WHERE username=?", (probe_name,)).fetchone()["id"])
    timed("按 Token（联表）", lambda: account_by_token(conn, token)["id"])
    timed("登录校验（含哈希）", lambda: "拒绝" if not authenticate(conn, probe_name, "wrong") else "命中")
    timed("成员分页 offset=50000", lambda: len(list_accounts(conn, limit=50, offset=50_000)))
    timed("昵称检索（FTS5）", lambda: len(search_accounts(conn, "研究者0500", limit=20)))
    timed("活跃成员计数", lambda: count_accounts(conn))

    path = Path(temp_dir) / "instance" / "hub.db"
    print(f"\n库体积：{path.stat().st_size / 1024 / 1024:.1f} MB  （{temp_dir}）")
    conn.close()


if __name__ == "__main__":
    main()
