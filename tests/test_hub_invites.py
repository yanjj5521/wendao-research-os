from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from hub.invites import (
    INVITE_ALPHABET,
    code_format_ok,
    consume_invite,
    find_invite,
    generate_invite_code,
    invite_expired,
    normalize_code,
)


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


class CodeNormalizationTests(unittest.TestCase):
    def test_bare_code(self) -> None:
        self.assertEqual(normalize_code("  ABC-123  "), "ABC-123")

    def test_full_registration_url(self) -> None:
        self.assertEqual(
            normalize_code("https://example.test/team/register?invite=ABC-123&x=1"),
            "ABC-123",
        )

    def test_copied_sentence_and_punctuation(self) -> None:
        self.assertEqual(normalize_code("邀请码：ABC-123。"), "ABC-123")
        self.assertEqual(normalize_code("注册码『ABC-123』"), "ABC-123")

    def test_full_width_and_spaces(self) -> None:
        self.assertEqual(normalize_code("ＡＢＣ１２３"), "ABC123")
        self.assertEqual(normalize_code(" A B C 1 2 3 "), "ABC123")

    def test_invisible_characters_are_removed(self) -> None:
        self.assertEqual(normalize_code("AB\u200bC-123\ufeff"), "ABC-123")

    def test_empty_and_garbage(self) -> None:
        self.assertEqual(normalize_code("   "), "")
        self.assertEqual(normalize_code("邀请码"), "")
        self.assertFalse(code_format_ok("ABC"))


class GeneratedCodeTests(unittest.TestCase):
    def test_generated_code_is_safe_and_fixed_length(self) -> None:
        codes = {generate_invite_code() for _ in range(20)}
        self.assertEqual(len(codes), 20)
        for code in codes:
            self.assertEqual(len(code), 10)
            self.assertTrue(set(code) <= set(INVITE_ALPHABET))
            self.assertNotIn("I", code)
            self.assertNotIn("O", code)
            self.assertNotIn("0", code)
            self.assertNotIn("1", code)


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

    def test_full_url_and_label_resolve_to_the_same_row(self) -> None:
        self._insert("AbC-123_x", 2, self.future)
        for raw in (
            "https://example.test/team/register?invite=AbC-123_x",
            "邀请码：AbC-123_x。",
        ):
            row, reason = find_invite(self.conn, raw)
            self.assertEqual(reason, "ok", raw)
            self.assertEqual(row["code"], "AbC-123_x")

    def test_legacy_row_with_spaces_is_found_and_consumed(self) -> None:
        self._insert(" ABC-123 ", 1, self.future)
        row, reason = find_invite(self.conn, "abc-123")
        self.assertEqual(reason, "ok")
        self.assertEqual(row["code"], " ABC-123 ")
        self.assertTrue(consume_invite(self.conn, row["code"]))
        self.assertFalse(consume_invite(self.conn, row["code"]))

    def test_invisible_character_in_stored_code_is_found(self) -> None:
        self._insert("AB\u200bC-123", 1, self.future)
        row, reason = find_invite(self.conn, "ABC-123")
        self.assertEqual(reason, "ok")
        self.assertEqual(row["code"], "AB\u200bC-123")

    def test_missing_empty_and_format_are_distinguished(self) -> None:
        self.assertEqual(find_invite(self.conn, "   ")[1], "empty")
        self.assertEqual(find_invite(self.conn, "ABC")[1], "format")
        self.assertEqual(find_invite(self.conn, "NOPE-123")[1], "missing")

    def test_used_and_expired_are_distinguished(self) -> None:
        self._insert("USED-1", 0, self.future)
        self._insert("OLD-123", 1, self.past)
        self.assertEqual(find_invite(self.conn, "USED-1")[1], "used")
        self.assertEqual(find_invite(self.conn, "OLD-123")[1], "expired")

    def test_consumption_is_atomic(self) -> None:
        self._insert("ONCE-1", 1, self.future)
        row, _ = find_invite(self.conn, "once-1")
        self.assertTrue(consume_invite(self.conn, row["code"]))
        self.assertFalse(consume_invite(self.conn, row["code"]))
        self.conn.commit()
        remaining = self.conn.execute(
            "SELECT uses_remaining FROM hub_invites WHERE code='ONCE-1'"
        ).fetchone()[0]
        self.assertEqual(remaining, 0)


if __name__ == "__main__":
    unittest.main()
