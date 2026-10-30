"""Resumable, hash-verified downloads shared by both update channels.

The hub path and the GitHub path both move a ~60 MB archive over links that may
be slow or drop mid-transfer.  Keeping a ``.part`` file plus a small sidecar
descriptor lets the next attempt continue with a Range request instead of
starting over.  A corrupt or mismatched part is discarded, never resumed.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

USER_AGENT = "WendaoResearch-updater"


class DownloadError(RuntimeError):
    """Raised when a download cannot be completed or verified."""


def _digest_file(path: Path) -> "hashlib._Hash":
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest


def _meta_path(part: Path) -> Path:
    return part.with_name(part.name + ".json")


def _resumable_offset(part: Path, meta_path: Path, url: str, expected: str) -> int:
    """Return a safe byte offset, deleting a part that cannot be trusted."""
    if not part.is_file() or not meta_path.is_file():
        part.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        return 0
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        meta = {}
    if (
        str(meta.get("url", "")) != url
        or str(meta.get("sha256", "")).lower() != expected.lower()
    ):
        part.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        return 0
    return part.stat().st_size


def fetch_resumable(
    url: str,
    destination: Path,
    *,
    expected_sha256: str,
    max_bytes: int,
    timeout: int,
    headers: dict[str, str] | None = None,
) -> Path:
    """Download ``url`` into ``destination``, resuming a previous attempt."""
    expected = (expected_sha256 or "").strip().lower()
    if len(expected) != 64:
        raise DownloadError("缺少有效的 SHA-256，已拒绝下载。")

    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + ".part")
    meta_path = _meta_path(part)
    offset = _resumable_offset(part, meta_path, url, expected)

    meta_path.write_text(
        json.dumps({"url": url, "sha256": expected}, ensure_ascii=False),
        encoding="utf-8",
    )

    request_headers = {"User-Agent": USER_AGENT}
    if headers:
        request_headers.update(headers)
    if offset:
        request_headers["Range"] = f"bytes={offset}-"

    digest = _digest_file(part) if offset else hashlib.sha256()
    total = offset
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=request_headers), timeout=timeout
        ) as response:
            status = getattr(response, "status", 200)
            if offset and status != 206:
                # Server ignored the range: start over rather than append twice.
                offset = 0
                total = 0
                digest = hashlib.sha256()
            mode = "ab" if offset else "wb"
            with part.open(mode) as out:
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        raise DownloadError("更新包超过 500 MB，已停止下载。")
                    digest.update(chunk)
                    out.write(chunk)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and offset:
            # The part may already be complete; hash verification decides below.
            digest = _digest_file(part)
        else:
            # Keep the part and its descriptor so the next click can resume.
            raise DownloadError(f"下载中断（HTTP {exc.code}），已保留进度，可重试续传。") from exc
    except urllib.error.URLError as exc:
        raise DownloadError("下载中断（网络不可用），已保留进度，可重试续传。") from exc
    except TimeoutError as exc:
        raise DownloadError("下载超时，已保留进度，可重试续传。") from exc

    if digest.hexdigest().lower() != expected:
        part.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        raise DownloadError("更新包 SHA-256 不匹配，已删除损坏的部分文件。")

    os.replace(part, destination)
    meta_path.unlink(missing_ok=True)
    return destination
