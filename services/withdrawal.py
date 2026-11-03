"""上传撤回机制（M1：自撤回 + 快照 + 日志）。

设计文档：docs/designs/upload-withdrawal.md

个人端没有独立管理员角色，设计中的"超边界转管理员审核"落地为：
超边界的撤回单进入 ``pending_review``，由本人在"撤回记录"里显式确认执行
（等价于自任管理员），全程留痕。M1 不触碰奖励账本（XP/灵石不动）。

自动判级（全部满足才自动执行）：
- 条目存在且状态为 active；
- 同一条目没有处理中/已执行的撤回单；
- 提交时间距条目创建 ≤ 自动窗口（settings 键 ``withdraw_window_hours``，默认 72h）；
- 错误类型在枚举内、说明 ≥10 字。
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from core.runtime_paths import STORAGE_ROOT

DEFAULT_WINDOW_HOURS = 72          # 自动撤回窗口，可经 settings 的 withdraw_window_hours 调整
ARCHIVE_AFTER_DAYS = 90            # 已完结撤回单的归档时限


def window_hours(conn: sqlite3.Connection) -> int:
    """自动撤回窗口（小时）：settings.withdraw_window_hours，默认 72，合法范围 1–8760。"""
    try:
        row = conn.execute("SELECT value FROM settings WHERE key='withdraw_window_hours'").fetchone()
        if row:
            return max(1, min(8760, int(row[0])))
    except (ValueError, TypeError, sqlite3.OperationalError):
        pass
    return DEFAULT_WINDOW_HOURS
MIN_DESCRIPTION = 10
MAX_TEAM_NOTE = 400

ERROR_TYPES: dict[str, str] = {
    "duplicate": "重复上传",
    "wrong_file": "传错文件",
    "wrong_workspace": "归档错位",
    "content_error": "内容有误",
    "privacy": "涉密/隐私",
    "other": "其他原因",
}

SNAPSHOT_DIR = STORAGE_ROOT / "withdrawal_snapshots"


class WithdrawalError(ValueError):
    """撤回流程的用户可读错误。"""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _entry_row(conn: sqlite3.Connection, entry_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()


def entry_withdrawal(conn: sqlite3.Connection, entry_id: int) -> dict[str, Any] | None:
    """条目当前挂着的撤回单（pending/approved 状态），无则 None。"""
    row = conn.execute(
        "SELECT * FROM upload_withdrawals WHERE entry_id=? "
        "AND status IN ('pending_review','approved') ORDER BY id DESC LIMIT 1",
        (entry_id,),
    ).fetchone()
    return dict(row) if row else None


def recent_withdrawals(conn: sqlite3.Connection, limit: int = 12) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT w.*, e.title AS entry_title, e.status AS entry_status "
        "FROM upload_withdrawals w LEFT JOIN entries e ON e.id = w.entry_id "
        "WHERE w.status <> 'archived' "
        "ORDER BY w.id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def _execute_withdrawal(
    conn: sqlite3.Connection, entry_id: int, withdrawal_id: int, decided_by: str
) -> None:
    """快照 → 文件副本 → 软删除 → 日志。调用方保证在事务里。"""
    entry = _entry_row(conn, entry_id)
    if entry is None:
        raise WithdrawalError("条目不存在，撤回单已作废。")
    entry_dict = {key: entry[key] for key in entry.keys()}
    rev_no = (
        conn.execute(
            "SELECT COALESCE(MAX(rev_no),0)+1 FROM entry_revisions WHERE entry_id=?",
            (entry_id,),
        ).fetchone()[0]
    )
    file_copy = ""
    file_path = str(entry_dict.get("file_path") or "").strip()
    if file_path:
        source = STORAGE_ROOT / "uploads" / file_path
        if source.is_file():
            SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            target = SNAPSHOT_DIR / f"entry{entry_id}_rev{rev_no}_{source.name}"
            shutil.copy2(source, target)
            file_copy = str(target)
    conn.execute(
        "INSERT INTO entry_revisions(entry_id,rev_no,snapshot_json,file_copy_path,note,created_at) "
        "VALUES (?,?,?,?,?,?)",
        (
            entry_id,
            rev_no,
            json.dumps(entry_dict, ensure_ascii=False, default=str),
            file_copy,
            f"撤回快照（撤回单 #{withdrawal_id}）",
            _now(),
        ),
    )
    conn.execute("UPDATE entries SET status='withdrawn', updated_at=? WHERE id=?", (_now(), entry_id))
    conn.execute(
        "UPDATE upload_withdrawals SET status='approved', decided_by=?, decided_at=? WHERE id=?",
        (decided_by, _now(), withdrawal_id),
    )
    conn.execute(
        "INSERT INTO activities(action, entry_id, xp, detail, created_at) VALUES (?,?,?,?,?)",
        ("withdraw", entry_id, 0, f"撤回：{entry_dict.get('title','')}（#{withdrawal_id}）", _now()),
    )


def archive_expired_withdrawals(conn: sqlite3.Connection, *, now: datetime | None = None) -> int:
    """把 90 天前已完结、且条目不再处于"已撤回"状态的撤回单标记为 archived。

    条目仍处于 withdrawn 状态的 approved 单不归档——否则会夺走用户的恢复入口。
    返回归档数量；在知识库页加载时惰性调用。
    """
    current = now or datetime.now().astimezone()
    horizon = (current - timedelta(days=ARCHIVE_AFTER_DAYS)).isoformat(timespec="seconds")
    cursor = conn.execute(
        "UPDATE upload_withdrawals SET status='archived' "
        "WHERE status IN ('approved','cancelled','obsolete') AND decided_at IS NOT NULL "
        "AND decided_at < ? "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM entries e WHERE e.id = upload_withdrawals.entry_id AND e.status='withdrawn'"
        ")",
        (horizon,),
    )
    return cursor.rowcount


def request_withdrawal(
    conn: sqlite3.Connection,
    entry_id: int,
    error_type: str,
    description: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """提交撤回申请：自动判级，边界内直接执行，越界转 pending_review。"""
    if error_type not in ERROR_TYPES:
        raise WithdrawalError("请选择有效的错误类型。")
    description = (description or "").strip()
    if len(description) < MIN_DESCRIPTION:
        raise WithdrawalError(f"错误说明至少 {MIN_DESCRIPTION} 个字，帮助我们改进。")
    entry = _entry_row(conn, entry_id)
    if entry is None:
        raise WithdrawalError("条目不存在或已被删除。")
    if entry["status"] == "withdrawn":
        raise WithdrawalError("该条目已经撤回过了。")
    if entry["status"] != "active":
        raise WithdrawalError(f"当前状态（{entry['status']}）不支持撤回。")
    if entry_withdrawal(conn, entry_id):
        raise WithdrawalError("该条目已有处理中的撤回单，请先在撤回记录里处理。")

    created = str(entry["created_at"])
    try:
        created_at = datetime.fromisoformat(created)
    except ValueError as exc:
        raise WithdrawalError("条目创建时间无法解析，已转人工确认。") from exc
    if created_at.tzinfo is None:
        created_at = created_at.astimezone()
    current = now or datetime.now().astimezone()
    within_window = (current - created_at) <= timedelta(hours=window_hours(conn))
    auto_level = "auto" if within_window else "manual_confirm"
    status = "approved" if within_window else "pending_review"

    cursor = conn.execute(
        "INSERT INTO upload_withdrawals(entry_id,error_type,description,status,auto_level,"
        "decided_by,decision_note,created_at) VALUES (?,?,?,?,?,?,?,?)",
        (
            entry_id,
            error_type,
            description[:MAX_TEAM_NOTE],
            status,
            auto_level,
            "system" if within_window else "",
            "边界内自动执行" if within_window else "超出自动撤回窗口，需人工确认",
            _now(),
        ),
    )
    withdrawal_id = int(cursor.lastrowid)  # type: ignore[arg-type]
    if within_window:
        _execute_withdrawal(conn, entry_id, withdrawal_id, "system")
    return {
        "id": withdrawal_id,
        "status": status,
        "auto_level": auto_level,
        "entry_title": entry["title"],
    }


def confirm_withdrawal(conn: sqlite3.Connection, withdrawal_id: int) -> dict[str, Any]:
    """越界撤回单的人工确认（个人端：用户自任管理员）。"""
    row = conn.execute(
        "SELECT * FROM upload_withdrawals WHERE id=?", (withdrawal_id,)
    ).fetchone()
    if not row:
        raise WithdrawalError("撤回单不存在。")
    if row["status"] != "pending_review":
        raise WithdrawalError("只有待确认的撤回单可以执行。")
    entry_id = int(row["entry_id"])
    entry = _entry_row(conn, entry_id)
    if entry is None or entry["status"] != "active":
        conn.execute(
            "UPDATE upload_withdrawals SET status='obsolete', decided_by='system', decided_at=? WHERE id=?",
            (_now(), withdrawal_id),
        )
        raise WithdrawalError("条目已不存在或状态已变化，撤回单已关闭。")
    conn.execute(
        "UPDATE upload_withdrawals SET decision_note=? WHERE id=?",
        ("人工确认执行（" + str(row["decision_note"]) + "）", withdrawal_id),
    )
    _execute_withdrawal(conn, entry_id, withdrawal_id, "owner-confirm")
    return {"id": withdrawal_id, "entry_id": entry_id, "status": "approved"}


def cancel_withdrawal(conn: sqlite3.Connection, withdrawal_id: int) -> None:
    row = conn.execute(
        "SELECT status FROM upload_withdrawals WHERE id=?", (withdrawal_id,)
    ).fetchone()
    if not row:
        raise WithdrawalError("撤回单不存在。")
    if row["status"] != "pending_review":
        raise WithdrawalError("只有待确认的撤回单可以取消。")
    conn.execute(
        "UPDATE upload_withdrawals SET status='cancelled', decided_by='owner', decided_at=? WHERE id=?",
        (_now(), withdrawal_id),
    )


def restore_entry(conn: sqlite3.Connection, entry_id: int) -> str:
    """从最近一次快照恢复条目（回滚也写日志）。"""
    entry = _entry_row(conn, entry_id)
    if entry is None:
        raise WithdrawalError("条目不存在。")
    if entry["status"] != "withdrawn":
        raise WithdrawalError("只有已撤回的条目可以恢复。")
    snapshot = conn.execute(
        "SELECT * FROM entry_revisions WHERE entry_id=? ORDER BY rev_no DESC LIMIT 1",
        (entry_id,),
    ).fetchone()
    if not snapshot:
        raise WithdrawalError("找不到可用的撤回快照。")
    data = json.loads(str(snapshot["snapshot_json"]))
    conn.execute("UPDATE entries SET status='active', updated_at=? WHERE id=?", (_now(), entry_id))
    conn.execute(
        "INSERT INTO activities(action, entry_id, xp, detail, created_at) VALUES (?,?,?,?,?)",
        ("withdraw_restore", entry_id, 0, f"从快照恢复：{data.get('title','')}", _now()),
    )
    return str(data.get("title", ""))
