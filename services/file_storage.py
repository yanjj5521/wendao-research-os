"""Bounded streaming helpers for user uploads."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import UploadFile


def _configured_upload_limit() -> int:
    raw = os.getenv("RESEARCH_OS_MAX_UPLOAD_BYTES", "").strip()
    if raw:
        try:
            return max(1024, int(raw))
        except ValueError:
            pass
    return 1024 * 1024 * 1024


MAX_UPLOAD_BYTES = _configured_upload_limit()
UPLOAD_CHUNK_BYTES = 1024 * 1024


class UploadLimitExceeded(ValueError):
    """Raised when an upload would exceed the configured byte budget."""


def save_upload_limited(
    upload: UploadFile,
    target: Path,
    *,
    max_bytes: int = MAX_UPLOAD_BYTES,
    remaining_bytes: int | None = None,
) -> int:
    """Stream an upload to disk while enforcing per-file and aggregate limits."""
    limit = min(max_bytes, remaining_bytes) if remaining_bytes is not None else max_bytes
    if limit <= 0:
        raise UploadLimitExceeded("本次上传已达到大小上限。")
    target.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with target.open("wb") as output:
            while True:
                chunk = upload.file.read(UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise UploadLimitExceeded(
                        f"本次上传超过 {limit / 1024 / 1024:.0f} MB 限制。"
                    )
                output.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return total
