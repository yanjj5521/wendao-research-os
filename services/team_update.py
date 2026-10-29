"""Member-side update preparation for a team release package.

The centre only hosts a signed-by-hash portable ZIP.  This module downloads
it, verifies the advertised SHA-256, backs up the local database, and writes
a short-lived Windows replacement script.  The script replaces only program
files after the current EXE exits; ``user_data`` is deliberately untouched.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
import urllib.request
import zipfile
from pathlib import Path

from core.runtime_paths import STORAGE_ROOT
from services.backups import backup_local_db


def _safe_package(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = [Path(name) for name in archive.namelist() if not name.endswith("/")]
        if not names or any(name.is_absolute() or ".." in name.parts for name in names):
            raise ValueError("更新包路径校验失败。")
        basenames = {name.name for name in names}
        required = {"portable.flag", "使用说明.txt"}
        exe_count = sum(name.name.lower() == "wendaoresearchv3.exe" for name in names)
        if exe_count != 1 or not required.issubset(basenames):
            raise ValueError("更新包不是有效的 Team-Update.zip。")


def prepare_team_update(*, hub_url: str, token: str, release_id: int, sha256: str) -> Path:
    if not hub_url or not token:
        raise ValueError("请先在同行会中配置中心地址与 Token。")
    update_dir = STORAGE_ROOT / "team_updates"
    update_dir.mkdir(parents=True, exist_ok=True)
    target = update_dir / f"team_update_{release_id}.zip"
    request = urllib.request.Request(
        f"{hub_url.rstrip('/')}/api/v1/releases/{release_id}/download",
        headers={"Authorization": f"Bearer {token}"},
    )
    digest = hashlib.sha256()
    with urllib.request.urlopen(request, timeout=60) as response, target.open("wb") as out:
        while chunk := response.read(1024 * 1024):
            digest.update(chunk)
            out.write(chunk)
    if digest.hexdigest().lower() != sha256.lower():
        target.unlink(missing_ok=True)
        raise ValueError("更新包 SHA-256 不匹配，已拒绝安装。")
    _safe_package(target)
    backup_local_db(force=True)
    return target


def schedule_windows_replace(package: Path) -> None:
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        raise RuntimeError("自动替换仅在已打包的 Windows EXE 中可用。")
    app_dir = Path(sys.executable).resolve().parent
    script = STORAGE_ROOT / "team_updates" / "apply_update.cmd"
    stage = STORAGE_ROOT / "team_updates" / "staging"
    script.write_text(
        "@echo off\r\n"
        "setlocal\r\n"
        f"timeout /t 3 /nobreak >nul\r\n"
        f"if exist \"{stage}\" rmdir /s /q \"{stage}\"\r\n"
        f"powershell -NoProfile -Command \"Expand-Archive -LiteralPath '{package}' -DestinationPath '{stage}' -Force\"\r\n"
        f"for /r \"{stage}\" %%F in (WendaoResearchV3.exe) do copy /y \"%%F\" \"{app_dir / 'WendaoResearchV3.exe'}\" >nul\r\n"
        f"start \"\" \"{app_dir / 'WendaoResearchV3.exe'}\"\r\n"
        "endlocal\r\n",
        encoding="utf-8",
    )
    subprocess.Popen(["cmd", "/c", str(script)], creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)


def exit_for_update() -> None:
    """Give the browser response time to leave, then release the EXE lock."""
    threading.Timer(1.5, lambda: os._exit(0)).start()
