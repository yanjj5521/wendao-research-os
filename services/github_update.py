"""Update directly from GitHub Releases for machines without a team hub.

The team centre remains the default channel.  This module is the fallback for a
standalone install: it reads the public repository's ``releases/latest``, picks
the user package, verifies the SHA-256 published in ``SHA256SUMS.txt`` and then
hands the archive to the same replacement routine used by the hub path.

Nothing here runs in the background: a user must press the button, so the
"local-first" promise is not weakened by silent network calls.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from core.runtime_paths import STORAGE_ROOT
from core.version import APP_VERSION, is_newer_version
from services.backups import backup_local_db
from services.team_update import APP_EXE_NAME, MAX_UPDATE_BYTES

GITHUB_REPO = "yanjj5521/wendao-research-os"
API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
DOWNLOAD_PREFIX = f"https://github.com/{GITHUB_REPO}/releases/download/"
USER_AGENT = "WendaoResearch-updater"
API_TIMEOUT = 10
DOWNLOAD_TIMEOUT = 300


class GithubUpdateError(RuntimeError):
    """A user-facing problem with the public update channel."""


def _open(url: str, *, timeout: int, accept: str = "") -> object:
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    request = urllib.request.Request(url, headers=headers)
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise GithubUpdateError(
                "GitHub 暂时限制了本次查询（每小时 60 次）。请稍后再试，或改用同行会更新。"
            ) from exc
        if exc.code == 404:
            raise GithubUpdateError("GitHub 上还没有正式发布版本。") from exc
        raise GithubUpdateError(f"GitHub 返回 HTTP {exc.code}。") from exc
    except urllib.error.URLError as exc:
        raise GithubUpdateError(
            "无法连接 GitHub。请检查网络或代理设置；不影响本地使用。"
        ) from exc


def latest_release() -> dict[str, object] | None:
    """Return the newest non-prerelease release, or None if it is unusable."""
    with _open(API_LATEST, timeout=API_TIMEOUT, accept="application/vnd.github+json") as response:
        payload = json.load(response)
    if payload.get("draft") or payload.get("prerelease"):
        return None
    tag = str(payload.get("tag_name", "")).strip()
    version = tag[1:] if tag[:1].lower() == "v" else tag
    if not version:
        return None
    assets = payload.get("assets") or []
    package = next(
        (a for a in assets if str(a.get("name", "")).endswith("-User.zip")), None
    )
    checksums = next(
        (a for a in assets if str(a.get("name", "")) == "SHA256SUMS.txt"), None
    )
    if not package or not checksums:
        return None
    package_url = str(package.get("browser_download_url", ""))
    checksum_url = str(checksums.get("browser_download_url", ""))
    if not package_url.startswith(DOWNLOAD_PREFIX) or not checksum_url.startswith(DOWNLOAD_PREFIX):
        raise GithubUpdateError("发布资产的下载地址不在预期仓库下，已拒绝。")
    return {
        "version": version,
        "tag": tag,
        "name": str(payload.get("name", "")),
        "notes": str(payload.get("body", ""))[:4000],
        "page": str(payload.get("html_url", "")),
        "published_at": str(payload.get("published_at", "")),
        "package_name": str(package.get("name", "")),
        "package_url": package_url,
        "package_size": int(package.get("size", 0) or 0),
        "checksum_url": checksum_url,
        "newer": is_newer_version(version, APP_VERSION),
    }


def _checksums(url: str) -> dict[str, str]:
    with _open(url, timeout=API_TIMEOUT) as response:
        text = response.read().decode("utf-8", errors="replace")
    result: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and len(parts[0]) == 64:
            result[parts[1].lstrip("*").strip()] = parts[0].lower()
    return result


def _validate_user_package(path: Path) -> None:
    """Accept the user package shape; unlike Team-Update it has no portable.flag."""
    with zipfile.ZipFile(path) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        names = [PurePosixPath(info.filename) for info in infos]
        if not names or any(name.is_absolute() or ".." in name.parts for name in names):
            raise GithubUpdateError("更新包路径校验失败，已拒绝安装。")
        if sum(max(0, int(info.file_size)) for info in infos) > MAX_UPDATE_BYTES:
            raise GithubUpdateError("更新包解压后超过 500 MB，已拒绝安装。")
        basenames = {name.name for name in names}
        exe_count = sum(name.name.lower() == APP_EXE_NAME.lower() for name in names)
        if exe_count != 1 or "使用说明.txt" not in basenames:
            raise GithubUpdateError("下载的包不是有效的用户发布包，已拒绝安装。")


def prepare_github_update(release: dict[str, object]) -> Path:
    """Download, verify and snapshot; the caller schedules the replacement."""
    version = str(release.get("version", "")).strip()
    package_url = str(release.get("package_url", ""))
    if not version or not package_url.startswith(DOWNLOAD_PREFIX):
        raise GithubUpdateError("发布信息不完整，已停止更新。")
    expected = _checksums(str(release.get("checksum_url", "")))
    package_name = str(release.get("package_name", ""))
    expected_hash = expected.get(package_name)
    if not expected_hash:
        raise GithubUpdateError("发布包缺少 SHA-256 记录，已拒绝安装。")

    update_dir = STORAGE_ROOT / "team_updates"
    update_dir.mkdir(parents=True, exist_ok=True)
    target = update_dir / f"github_update_{version}.zip"
    partial = update_dir / f"github_update_{version}.zip.part"
    partial.unlink(missing_ok=True)
    digest = hashlib.sha256()
    total = 0
    try:
        with _open(package_url, timeout=DOWNLOAD_TIMEOUT) as response, partial.open("wb") as out:
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPDATE_BYTES:
                    raise GithubUpdateError("更新包超过 500 MB，已停止下载。")
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest().lower() != expected_hash:
            raise GithubUpdateError("更新包 SHA-256 与发布记录不匹配，已拒绝安装。")
        _validate_user_package(partial)
        os.replace(partial, target)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    backup = backup_local_db(force=True)
    if backup is None:
        raise GithubUpdateError("更新前数据库快照未能创建，已停止更新。")
    return target
