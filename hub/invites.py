"""Invite-code lookup, expiry parsing and atomic consumption.

Kept outside the route so the edge cases (legacy naive timestamps, malformed
values, case-insensitive typing, concurrent use) can be tested directly.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime


def normalize_code(raw: str | None) -> str:
    return (raw or "").strip()


def invite_expired(value: str | None, now: datetime | None = None) -> bool:
    """Treat missing expiry as permanent; malformed values as unusable.

    Legacy rows may store a naive ISO timestamp.  Comparing it directly with an
    aware ``datetime.now()`` raised ``TypeError`` and turned registration into a
    500 page, so naive values are interpreted in the local timezone.
    """
    if not value:
        return False
    try:
        expires = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return True
    current = now or datetime.now().astimezone()
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=current.tzinfo)
    return expires < current


def find_invite(
    conn: sqlite3.Connection, raw_code: str | None, now: datetime | None = None
) -> tuple[dict | None, str]:
    """Return ``(row, reason)`` where reason is ok/empty/missing/used/expired."""
    code = normalize_code(raw_code)
    if not code:
        return None, "empty"
    row = conn.execute(
        "SELECT * FROM hub_invites WHERE code = ? COLLATE NOCASE ORDER BY created_at DESC LIMIT 1",
        (code,),
    ).fetchone()
    if not row:
        return None, "missing"
    if int(row["uses_remaining"] or 0) <= 0:
        return None, "used"
    if invite_expired(row["expires_at"], now):
        return None, "expired"
    return dict(row), "ok"


def consume_invite(conn: sqlite3.Connection, canonical_code: str) -> bool:
    """Atomically decrement one use; false when another request took the last one."""
    cursor = conn.execute(
        "UPDATE hub_invites SET uses_remaining = uses_remaining - 1 "
        "WHERE code = ? AND uses_remaining > 0",
        (canonical_code,),
    )
    return cursor.rowcount == 1
