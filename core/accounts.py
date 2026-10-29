"""中心端账号仓储：身份、凭据、档案的唯一读写入口。

设计要点
--------
1. **三层分离**：`accounts` 只放"这个人是谁"，`account_credentials` 放口令与
   Token，`account_profiles` 放可公开的档案。改口令策略、加二次验证、扩档案
   字段时，各自独立演进。
2. **查询走索引**：用户名与 Token 都是唯一索引；成员列表按 `role/status` 与
   `created_at` 建复合索引；模糊查找走 FTS5，不做 `LIKE '%q%'` 全表扫描。
3. **口令参数随行存储**：算法与迭代次数写在凭据行里，将来提高迭代次数或换
   算法时，老账号仍可用原参数校验，不需要一次性重置所有密码。
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from typing import Any

PASSWORD_ALGO = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 310_000
MIN_PASSWORD_LENGTH = 8
USERNAME_RE = re.compile(r"^[a-z0-9_-]{3,32}$")


class AccountError(ValueError):
    """账号相关的可预期错误（用户名不合法、口令太短等）。"""


def now_iso() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="seconds")


def hash_password(
    password: str,
    *,
    salt: bytes | None = None,
    iterations: int = DEFAULT_ITERATIONS,
) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return salt.hex(), digest.hex()


def verify_password(
    password: str,
    salt_hex: str,
    digest_hex: str,
    iterations: int = DEFAULT_ITERATIONS,
) -> bool:
    _, candidate = hash_password(password, salt=bytes.fromhex(salt_hex), iterations=iterations)
    return secrets.compare_digest(candidate, digest_hex)


def normalize_username(value: str) -> str:
    username = (value or "").strip().lower()
    if not USERNAME_RE.match(username):
        raise AccountError("用户名需为3—32位小写字母、数字、下划线或短横线。")
    return username


def _check_password(password: str) -> None:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AccountError(f"密码至少{MIN_PASSWORD_LENGTH}位。")


def create_account(
    conn: sqlite3.Connection,
    username: str,
    password: str,
    display_name: str = "",
    role: str = "member",
    email: str | None = None,
) -> tuple[int, str]:
    """创建一个账号，返回 (account_id, api_token)。"""
    username = normalize_username(username)
    _check_password(password)
    if role not in {"admin", "member"}:
        raise AccountError("角色只能是 admin 或 member。")
    salt, digest = hash_password(password)
    token = secrets.token_urlsafe(32)
    ts = now_iso()
    cursor = conn.execute(
        """
        INSERT INTO accounts(username,display_name,email,role,status,created_at,updated_at)
        VALUES (?,?,?,?,'active',?,?)
        """,
        (username, (display_name or "").strip() or username, (email or "").strip() or None, role, ts, ts),
    )
    account_id = int(cursor.lastrowid)
    conn.execute(
        """
        INSERT INTO account_credentials(
            account_id,password_algo,password_iterations,password_salt,password_hash,
            api_token,token_rotated_at,updated_at
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (account_id, PASSWORD_ALGO, DEFAULT_ITERATIONS, salt, digest, token, ts, ts),
    )
    conn.execute(
        """
        INSERT INTO account_profiles(
            account_id,title,bio,skills,capabilities,goals,avatar_symbol,theme_json,
            public,revision,updated_at
        ) VALUES (?,?,?,?,?,?,?,?,1,1,?)
        """,
        (
            account_id,
            "科研修士",
            "正在建立自己的科研体系。",
            "",
            "",
            "",
            "道",
            json.dumps(
                {
                    "accent": "terracotta",
                    "density": "comfortable",
                    "scene": "warm",
                    "home_motto": "把学习变成可以积累的事",
                    "home_poem": "纸上得来终觉浅，绝知此事要躬行。——陆游",
                },
                ensure_ascii=False,
            ),
            ts,
        ),
    )
    return account_id, token


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> sqlite3.Row | None:
    """按用户名 + 口令校验，成功返回身份行（含 role/status）。"""
    username = (username or "").strip().lower()
    row = conn.execute(
        """
        SELECT a.*, c.password_salt, c.password_hash, c.password_iterations
        FROM accounts a JOIN account_credentials c ON c.account_id = a.id
        WHERE a.username = ? AND a.status = 'active'
        """,
        (username,),
    ).fetchone()
    if not row:
        return None
    ok = verify_password(
        password,
        row["password_salt"],
        row["password_hash"],
        int(row["password_iterations"] or DEFAULT_ITERATIONS),
    )
    return row if ok else None


def account_by_token(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    token = (token or "").strip()
    if not token:
        return None
    return conn.execute(
        """
        SELECT a.* FROM accounts a
        JOIN account_credentials c ON c.account_id = a.id
        WHERE c.api_token = ? AND a.status = 'active'
        """,
        (token,),
    ).fetchone()


def credentials_for(conn: sqlite3.Connection, account_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM account_credentials WHERE account_id = ?", (int(account_id),)
    ).fetchone()


def set_password(conn: sqlite3.Connection, account_id: int, password: str) -> None:
    _check_password(password)
    salt, digest = hash_password(password)
    conn.execute(
        """
        UPDATE account_credentials
        SET password_algo=?,password_iterations=?,password_salt=?,password_hash=?,updated_at=?
        WHERE account_id=?
        """,
        (PASSWORD_ALGO, DEFAULT_ITERATIONS, salt, digest, now_iso(), int(account_id)),
    )


def rotate_token(conn: sqlite3.Connection, account_id: int) -> str:
    token = secrets.token_urlsafe(32)
    ts = now_iso()
    conn.execute(
        "UPDATE account_credentials SET api_token=?,token_rotated_at=?,updated_at=? WHERE account_id=?",
        (token, ts, ts, int(account_id)),
    )
    return token


def touch(conn: sqlite3.Connection, account_id: int) -> None:
    ts = now_iso()
    conn.execute("UPDATE accounts SET last_seen_at=?,updated_at=? WHERE id=?", (ts, ts, int(account_id)))


def mark_login(conn: sqlite3.Connection, account_id: int) -> None:
    ts = now_iso()
    conn.execute(
        "UPDATE accounts SET last_login_at=?,last_seen_at=?,updated_at=? WHERE id=?",
        (ts, ts, ts, int(account_id)),
    )


def set_status(conn: sqlite3.Connection, account_id: int, status: str) -> None:
    if status not in {"active", "disabled", "pending"}:
        raise AccountError("未知的账号状态。")
    conn.execute(
        "UPDATE accounts SET status=?,updated_at=? WHERE id=?", (status, now_iso(), int(account_id))
    )


def toggle_active(conn: sqlite3.Connection, account_id: int) -> str:
    row = conn.execute("SELECT status FROM accounts WHERE id=?", (int(account_id),)).fetchone()
    if not row:
        raise AccountError("账号不存在。")
    target = "disabled" if row["status"] == "active" else "active"
    set_status(conn, account_id, target)
    return target


def count_accounts(conn: sqlite3.Connection, *, role: str = "", status: str = "active") -> int:
    sql = "SELECT COUNT(*) n FROM accounts WHERE 1=1"
    params: list[Any] = []
    if role:
        sql += " AND role=?"
        params.append(role)
    if status:
        sql += " AND status=?"
        params.append(status)
    return int(conn.execute(sql, params).fetchone()["n"])


def list_accounts(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
    offset: int = 0,
    include_disabled: bool = True,
) -> list[sqlite3.Row]:
    """成员列表：先按角色（admin 字母序在前）再按创建时间。

    `role` 直接参与排序，才能命中 (role, created_at DESC) 复合索引；
    写成 `role='admin' DESC` 会让 SQLite 放弃索引、退化为全表扫描。
    """
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    sql = "SELECT * FROM accounts"
    if not include_disabled:
        sql += " WHERE status='active'"
    sql += " ORDER BY role, created_at DESC, id DESC LIMIT ? OFFSET ?"
    return list(conn.execute(sql, (limit, offset)))


def search_accounts(conn: sqlite3.Connection, query: str, *, limit: int = 20) -> list[sqlite3.Row]:
    """用户名/昵称检索：优先 FTS5，查不到再退回前缀匹配。"""
    text = (query or "").strip()
    if not text:
        return []
    limit = max(1, min(int(limit), 100))
    try:
        rows = conn.execute(
            """
            SELECT a.* FROM accounts_fts f
            JOIN accounts a ON a.id = f.rowid
            WHERE accounts_fts MATCH ?
            ORDER BY rank LIMIT ?
            """,
            (f'"{text}"*', limit),
        ).fetchall()
        if rows:
            return list(rows)
    except sqlite3.OperationalError:
        pass
    return list(
        conn.execute(
            "SELECT * FROM accounts WHERE username LIKE ? OR display_name LIKE ? ORDER BY id LIMIT ?",
            (f"{text}%", f"{text}%", limit),
        )
    )


def update_profile(conn: sqlite3.Connection, account_id: int, **fields: Any) -> None:
    allowed = {"title", "bio", "skills", "capabilities", "goals", "avatar_symbol", "theme_json", "public"}
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return
    sets = ",".join(f"{key}=?" for key in updates)
    conn.execute(
        f"UPDATE account_profiles SET {sets},revision=revision+1,updated_at=? WHERE account_id=?",
        (*updates.values(), now_iso(), int(account_id)),
    )
