from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from hub.invites import consume_invite, find_invite, invite_expired, normalize_code


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE hub_invites(
            code TEXT PRIMARY KEY,
            uses_remaining INTEGER NOT NULL DEFAULT 1,
            expires_at TEXT,
            created_by INTEGER,
            created_at TEXT NOT NULL
        )"""
    )
    return conn


class InviteExpiryTests(unittest.TestCase):
    def test_missing_expiry_is_permanent(self) -> None:
        self.assertFalse(invite_expired(None))
        self.assertFalse(invite_expired(""))

    def test_naive_legacy_timestamp_does_not_crash(self) -> None:
        now = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
        self.assertTrue(invite_expired("2020-01-01T00:00:00", now))
        self.assertFalse(invite_expired("2099-01-01T00:00:00", now))

    def test_aware_timestamp_is_compared(self) -> None:
        now = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
        self.assertTrue(invite_expired("2026-11-01T11:59:00+00:00", now))
        self.assertFalse(invite_expired("2026-11-01T12:01:00+00:00", now))

    def test_malformed_expiry_is_unusable(self) -> None:
        self.assertTrue(invite_expired("not-a-date"))


class InviteLookupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = _connection()
        self.future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        self.past = "2020-01-01T00:00:00"

    def tearDown(self) -> None:
        self.conn.close()

    def _insert(self, code: str, uses: int, expires: str | None) -> None:
        self.conn.execute(
            "INSERT INTO hub_invites(code,uses_remaining,expires_at,created_by,created_at) "
            "VALUES (?,?,?,?,?)",
            (code, uses, expires, 1, "2026-11-01T00:00:00+08:00"),
        )
        self.conn.commit()

    def test_code_is_trimmed_and_case_insensitive(self) -> None:
        self._insert("AbC-123_x", 1, self.future)
        row, reason = find_invite(self.conn, "  abc-123_X ")
        self.assertEqual(reason, "ok")
        self.assertEqual(row["code"], "AbC-123_x")

    def test_missing_and_empty_are_distinguished(self) -> None:
        self.assertEqual(find_invite(self.conn, "   ")[1], "empty")
        self.assertEqual(find_invite(self.conn, "NOPE")[1], "missing")

    def test_used_and_expired_are_distinguished(self) -> None:
        self._insert("USED", 0, self.future)
        self._insert("OLD", 1, self.past)
        self.assertEqual(find_invite(self.conn, "USED")[1], "used")
        self.assertEqual(find_invite(self.conn, "OLD")[1], "expired")

    def test_consumption_is_atomic(self) -> None:
        self._insert("ONCE", 1, self.future)
        row, _ = find_invite(self.conn, "once")
        self.assertTrue(consume_invite(self.conn, row["code"]))
        self.assertFalse(consume_invite(self.conn, row["code"]))
        self.conn.commit()
        remaining = self.conn.execute(
            "SELECT uses_remaining FROM hub_invites WHERE code='ONCE'"
        ).fetchone()[0]
        self.assertEqual(remaining, 0)

    def test_normalize_code_only_strips_edges(self) -> None:
        self.assertEqual(normalize_code("  a b  "), "a b")


if __name__ == "__main__":
    unittest.main()
