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
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Sequence

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


def _opener(proxy: str):
    if proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    return None


def _probe(url: str, *, proxy: str, headers: dict[str, str], timeout: float = 8.0, sample: int = 512 * 1024):
    """下载前 512 KB 测速：返回 (速度字节/秒, 总大小或 None, 是否支持分段)。"""
    request = urllib.request.Request(url, headers={**headers, "Range": f"bytes=0-{sample - 1}"})
    opener = _opener(proxy)
    started = time.monotonic()
    try:
        response = opener.open(request, timeout=timeout) if opener else urllib.request.urlopen(request, timeout=timeout)
        with response as handle:
            status = getattr(handle, "status", 200)
            chunk = handle.read(sample)
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    elapsed = max(0.05, time.monotonic() - started)
    if not chunk:
        return None
    total = None
    content_range = ""
    try:
        content_range = handle.headers.get("Content-Range", "")  # type: ignore[union-attr]
    except Exception:
        content_range = ""
    if content_range and "/" in content_range:
        try:
            total = int(content_range.rsplit("/", 1)[1])
        except ValueError:
            total = None
    supports_range = status == 206
    return (len(chunk) / elapsed, total, supports_range)


def _fetch_parallel(
    url: str,
    destination: Path,
    *,
    total: int,
    max_bytes: int,
    expected_sha256: str,
    headers: dict[str, str],
    proxy: str,
    timeout: int,
    workers: int = 2,
) -> Path:
    """把文件切成 workers 段并发下载，最后顺序拼接并校验。"""
    if total <= 0 or total > max_bytes or workers < 1 or workers > total:
        raise DownloadError("更新包大小不合法或超过上限。")
    parts = [destination.with_name(f"{destination.name}.part{index}") for index in range(workers)]
    assembled = destination.with_name(destination.name + ".parallel.tmp")
    opener = _opener(proxy)

    def download_part(index: int) -> Path:
        start = total * index // workers
        end = total * (index + 1) // workers - 1
        part = parts[index]
        request = urllib.request.Request(
            url, headers={**headers, "Range": f"bytes={start}-{end}"}
        )
        response = opener.open(request, timeout=timeout) if opener else urllib.request.urlopen(request, timeout=timeout)
        with response as handle:
            content_range = str(handle.headers.get("Content-Range", "")).strip()
            match = re.fullmatch(r"bytes\s+(\d+)-(\d+)/(\d+)", content_range, re.IGNORECASE)
            if (getattr(handle, "status", 200) != 206 or not match
                    or tuple(map(int, match.groups())) != (start, end, total)):
                raise DownloadError(f"分段 {index + 1} 的范围无效，已回退单连接下载。")
            part_size = end - start + 1
            written = 0
            with part.open("wb") as out:
                while chunk := handle.read(min(1024 * 1024, part_size - written + 1)):
                    if written + len(chunk) > part_size:
                        raise DownloadError(f"分段 {index + 1} 超出范围，已停止下载。")
                    out.write(chunk)
                    written += len(chunk)
            if written != part_size:
                raise DownloadError(f"分段 {index + 1} 大小不符，已回退单连接下载。")
        return part

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(download_part, range(workers)))

        digest = hashlib.sha256()
        with assembled.open("wb") as out:
            for part in parts:
                with part.open("rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        digest.update(chunk)
                        out.write(chunk)
        if digest.hexdigest().lower() != expected_sha256.lower():
            raise DownloadError("更新包 SHA-256 不匹配，已删除损坏文件。")
        os.replace(assembled, destination)
        return destination
    finally:
        for part in parts:
            part.unlink(missing_ok=True)
        assembled.unlink(missing_ok=True)


def fetch_fast(
    url: str,
    destination: Path,
    *,
    expected_sha256: str,
    max_bytes: int,
    timeout: int,
    headers: dict[str, str] | None = None,
    proxy: str = "",
    mirror_urls: Sequence[str] = (),
) -> Path:
    """先测速选最快线路，再尝试双路并发；失败时回退到可续传的单连接。"""
    expected = (expected_sha256 or "").strip().lower()
    if len(expected) != 64:
        raise DownloadError("缺少有效的 SHA-256，已拒绝下载。")
    destination.parent.mkdir(parents=True, exist_ok=True)
    base_headers = {"User-Agent": USER_AGENT}
    if headers:
        base_headers.update(headers)

    best_url = url
    best_total = None
    best_speed = -1.0
    best_range = False
    for candidate in [url, *mirror_urls]:
        result = _probe(candidate, proxy=proxy, headers=base_headers)
        if not result:
            continue
        speed, total, supports_range = result
        if total and total > max_bytes:
            raise DownloadError("更新包超过上限，已停止下载。")
        if speed > best_speed:
            best_url, best_total, best_speed, best_range = candidate, total, speed, supports_range

    if best_speed < 0:
        # 测速全部失败：交给单连接下载器给出统一的可读错误/续传能力。
        return fetch_resumable(
            url, destination, expected_sha256=expected, max_bytes=max_bytes,
            timeout=timeout, headers=headers, proxy=proxy,
        )

    if best_range and best_total and best_total > 4 * 1024 * 1024:
        try:
            return _fetch_parallel(
                best_url, destination, total=best_total, max_bytes=max_bytes, expected_sha256=expected,
                headers=base_headers, proxy=proxy, timeout=timeout, workers=2,
            )
        except Exception:
            pass  # 回退单连接，保留可用性

    return fetch_resumable(
        best_url, destination, expected_sha256=expected, max_bytes=max_bytes,
        timeout=timeout, headers=headers, proxy=proxy,
    )
