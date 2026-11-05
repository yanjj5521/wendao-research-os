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


def file_sha256(path: Path) -> str:
    return _digest_file(path).hexdigest().lower()


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
    proxy: str = "",
) -> Path:
    """Download ``url`` into ``destination``, resuming a previous attempt.

    Any transient network failure (timeout, reset, disconnect) keeps the
    ``.part`` file and retries once before surfacing a user-facing error.
    """
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

    base_headers = {"User-Agent": USER_AGENT}
    if headers:
        base_headers.update(headers)

    digest = _digest_file(part) if offset else hashlib.sha256()
    total = offset
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            request_headers = dict(base_headers)
            if total:
                request_headers["Range"] = f"bytes={total}-"
            request = urllib.request.Request(url, headers=request_headers)
            # 没配代理时仍走 urlopen：行为与旧版一致，也便于测试替换。
            if proxy:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({"http": proxy, "https": proxy})
                )
                response_ctx = opener.open(request, timeout=timeout)
            else:
                response_ctx = urllib.request.urlopen(request, timeout=timeout)
            with response_ctx as response:
                status = getattr(response, "status", 200)
                if total and status != 206:
                    # Server ignored the range: start over rather than append twice.
                    total = 0
                    digest = hashlib.sha256()
                mode = "ab" if total else "wb"
                with part.open(mode) as out:
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > max_bytes:
                            raise DownloadError("更新包超过 500 MB，已停止下载。")
                        digest.update(chunk)
                        out.write(chunk)
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and total:
                # The part may already be complete; hash verification decides below.
                digest = _digest_file(part)
                break
            # Keep the part and its descriptor so the next click can resume.
            raise DownloadError(f"下载中断（HTTP {exc.code}），已保留进度，可重试续传。") from exc
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            # RemoteDisconnected and friends are OSErrors that urllib does not
            # always wrap into URLError.
            last_error = exc
            total = part.stat().st_size if part.is_file() else 0
            digest = _digest_file(part) if total else hashlib.sha256()
    else:
        raise DownloadError("下载中断（网络超时或不稳定），已保留进度，可重试续传。") from last_error

    if digest.hexdigest().lower() != expected:
        part.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        raise DownloadError("更新包 SHA-256 不匹配，已删除损坏的部分文件。")

    os.replace(part, destination)
    meta_path.unlink(missing_ok=True)
    return destination
