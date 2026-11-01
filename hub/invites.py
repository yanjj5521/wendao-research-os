"""Invite-code normalization, lookup, expiry parsing and atomic consumption.

The registration form is a human input surface: people paste the whole invite
URL, a sentence such as ``邀请码：ABC``, or text copied from a chat client with
invisible characters.  Validation therefore normalizes first and only then
queries the database; comparing the raw field was the reason a valid code could
be reported as "邀请码无效".
"""
from __future__ import annotations

import re
import secrets
import sqlite3
from datetime import datetime
from unicodedata import normalize as unicode_normalize
from urllib.parse import unquote

CODE_LENGTH = 10
# No I/O/0/1: these are the characters people most often mistype.
INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_RE = re.compile(r"[A-Za-z0-9_-]{6,64}")
_STRIP = "\"'`<>()[]{}，。,;；:：、"
_INVISIBLE_RE = re.compile(r"[\s\u200b\u200c\u200d\ufeff]+")


def generate_invite_code(length: int = CODE_LENGTH) -> str:
    length = max(6, min(32, int(length)))
    return "".join(secrets.choice(INVITE_ALPHABET) for _ in range(length))


def normalize_code(raw: str | None) -> str:
    """Accept a bare code, a full register URL, or a copied sentence."""
    text = unicode_normalize("NFKC", str(raw or "")).strip()
    if not text:
        return ""
    marker = text.lower().find("invite=")
    if marker >= 0:
        text = text[marker + len("invite=") :]
        text = text.split("&", 1)[0].split("#", 1)[0]
        text = unquote(text)
    text = _INVISIBLE_RE.sub("", text)
    text = text.strip(_STRIP)
    matches = _CODE_RE.findall(text)
    if matches:
        text = max(matches, key=len)
    elif not re.search(r"[A-Za-z0-9_-]", text):
        text = ""
    return text


def code_format_ok(code: str) -> bool:
    return bool(_CODE_RE.fullmatch(code or ""))


def invite_expired(value: str | None, now: datetime | None = None) -> bool:
    """Treat missing expiry as permanent; malformed values as unusable."""
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
    """Return ``(row, reason)`` where reason is ok/empty/format/missing/used/expired."""
    code = normalize_code(raw_code)
    if not code:
        return None, "empty"
    if not code_format_ok(code):
        return None, "format"
    row = conn.execute(
        "SELECT * FROM hub_invites WHERE TRIM(code) = ? COLLATE NOCASE "
        "ORDER BY created_at DESC LIMIT 1",
        (code,),
    ).fetchone()
    if not row:
        # Legacy rows may contain invisible characters or internal whitespace.
        # The table is intentionally small, so a normalized scan is cheap and
        # lets old codes keep working after this change.
        for candidate in conn.execute("SELECT * FROM hub_invites"):
            if normalize_code(candidate["code"]).casefold() == code.casefold():
                row = candidate
                break
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
