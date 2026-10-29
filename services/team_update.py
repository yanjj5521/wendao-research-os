"""Member-side download and safe in-place installation of team releases.

The centre hosts a hash-verified portable ZIP.  An update replaces only the
single program EXE; the persistent data directory is never included in the
update path.  Before replacement the local SQLite database is snapshotted, and
the previous EXE is kept briefly so a failed launch can be rolled back.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import threading
import urllib.request
import zipfile
from pathlib import Path

from core.runtime_paths import STORAGE_ROOT
from services.backups import backup_local_db

MAX_UPDATE_BYTES = 500 * 1024 * 1024
APP_EXE_NAME = "WendaoResearchV3.exe"


def can_self_update() -> bool:
    """In-place EXE replacement is available only in the frozen Windows app."""
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def _safe_package(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        names = [Path(info.filename) for info in infos]
        if not names or any(name.is_absolute() or ".." in name.parts for name in names):
            raise ValueError("更新包路径校验失败。")
        if sum(max(0, int(info.file_size)) for info in infos) > MAX_UPDATE_BYTES:
            raise ValueError("更新包解压后超过 500 MB，已拒绝安装。")
        basenames = {name.name for name in names}
        required = {"portable.flag", "使用说明.txt"}
        exe_count = sum(name.name.lower() == APP_EXE_NAME.lower() for name in names)
        if exe_count != 1 or not required.issubset(basenames):
            raise ValueError("更新包不是有效的 Team-Update.zip。")


def prepare_team_update(*, hub_url: str, token: str, release_id: int, sha256: str) -> Path:
    """Download, bounds-check, hash-verify and backup before returning a package."""
    if not hub_url or not token:
        raise ValueError("请先在同行会中配置中心地址与 Token。")
    update_dir = STORAGE_ROOT / "team_updates"
    update_dir.mkdir(parents=True, exist_ok=True)
    target = update_dir / f"team_update_{release_id}.zip"
    partial = update_dir / f"team_update_{release_id}.zip.part"
    partial.unlink(missing_ok=True)
    request = urllib.request.Request(
        f"{hub_url.rstrip('/')}/api/v1/releases/{release_id}/download",
        headers={"Authorization": f"Bearer {token}"},
    )
    digest = hashlib.sha256()
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=300) as response, partial.open("wb") as out:
            content_length = str(response.headers.get("Content-Length", "")).strip()
            if content_length.isdigit() and int(content_length) > MAX_UPDATE_BYTES:
                raise ValueError("更新包超过 500 MB，已拒绝下载。")
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPDATE_BYTES:
                    raise ValueError("更新包超过 500 MB，已停止下载。")
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest().lower() != sha256.lower():
            raise ValueError("更新包 SHA-256 不匹配，已拒绝安装。")
        _safe_package(partial)
        os.replace(partial, target)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    backup = backup_local_db(force=True)
    if backup is None:
        raise RuntimeError("更新前数据库快照未能创建，已停止更新。")
    return target


UPDATE_SCRIPT = r"""param(
    [Parameter(Mandatory=$true)][int]$ParentPid,
    [Parameter(Mandatory=$true)][string]$Executable,
    [Parameter(Mandatory=$true)][string]$Package,
    [Parameter(Mandatory=$true)][string]$Stage,
    [Parameter(Mandatory=$true)][string]$LogPath
)
$ErrorActionPreference = "Stop"
$previous = "$Executable.previous"
$incoming = Join-Path (Split-Path -Parent $Executable) ".WendaoResearchV3.update.exe"

function Write-UpdateLog([string]$Message) {
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $LogPath -Value "$stamp $Message" -Encoding UTF8
}

function Get-Sha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    try {
        $sha = [Security.Cryptography.SHA256]::Create()
        try { return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace("-", "") }
        finally { $sha.Dispose() }
    } finally { $stream.Dispose() }
}

try {
    New-Item -ItemType Directory -Path (Split-Path -Parent $LogPath) -Force | Out-Null
    Write-UpdateLog "waiting for parent process $ParentPid"
    try { Wait-Process -Id $ParentPid -Timeout 25 -ErrorAction Stop } catch {}
    Start-Sleep -Milliseconds 800
    if (-not (Test-Path -LiteralPath $Package)) { throw "update package is missing" }
    if (Test-Path -LiteralPath $Stage) { Remove-Item -LiteralPath $Stage -Recurse -Force }
    New-Item -ItemType Directory -Path $Stage -Force | Out-Null
    Expand-Archive -LiteralPath $Package -DestinationPath $Stage -Force
    $newExe = Get-ChildItem -LiteralPath $Stage -Filter "WendaoResearchV3.exe" -File -Recurse |
        Select-Object -First 1
    if (-not $newExe) { throw "new executable is missing from package" }
    Get-Process -Name "WendaoResearchV3" -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 800
    Copy-Item -LiteralPath $Executable -Destination $previous -Force
    Copy-Item -LiteralPath $newExe.FullName -Destination $incoming -Force
    $sourceHash = Get-Sha256 $incoming
    [IO.File]::Copy($incoming, $Executable, $true)
    $targetHash = Get-Sha256 $Executable
    if ($sourceHash -ne $targetHash) { throw "copied executable hash mismatch" }
    Remove-Item -LiteralPath $incoming -Force -ErrorAction SilentlyContinue
    Write-UpdateLog "replacement complete; starting new version"
    $started = Start-Process -FilePath $Executable -WorkingDirectory (Split-Path -Parent $Executable) -PassThru
    Start-Sleep -Seconds 8
    if ($started.HasExited) { throw "new version exited during startup" }
    Remove-Item -LiteralPath $previous -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $Package -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $Stage -Recurse -Force -ErrorAction SilentlyContinue
    Write-UpdateLog "update succeeded"
} catch {
    Write-UpdateLog ("update failed: " + $_.Exception.Message)
    try {
        if (Test-Path -LiteralPath $previous) {
            Get-Process -Name "WendaoResearchV3" -ErrorAction SilentlyContinue |
                Stop-Process -Force -ErrorAction SilentlyContinue
            Start-Sleep -Milliseconds 500
            Copy-Item -LiteralPath $previous -Destination $Executable -Force
            Start-Process -FilePath $Executable -WorkingDirectory (Split-Path -Parent $Executable)
            Write-UpdateLog "previous executable restored"
        }
    } catch {
        Write-UpdateLog ("rollback failed: " + $_.Exception.Message)
    }
    exit 1
}
"""


def schedule_windows_replace(package: Path) -> None:
    if not can_self_update():
        raise RuntimeError("自动替换仅在已打包的 Windows EXE 中可用。")
    app_dir = Path(sys.executable).resolve().parent
    update_dir = STORAGE_ROOT / "team_updates"
    stage = update_dir / "staging"
    log_path = update_dir / "apply_update.log"
    script = update_dir / "apply_update.ps1"
    update_dir.mkdir(parents=True, exist_ok=True)
    script.write_text(UPDATE_SCRIPT, encoding="utf-8-sig")
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if not powershell:
        raise RuntimeError("未找到 PowerShell，无法启动安全更新。")
    # DETACHED_PROCESS combined with -WindowStyle Hidden exits immediately on
    # current Windows PowerShell 5.1 without running the script.  A hidden
    # child process still outlives the exiting desktop app when only
    # CREATE_NO_WINDOW is used.
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(script),
            "-ParentPid",
            str(os.getpid()),
            "-Executable",
            str(app_dir / APP_EXE_NAME),
            "-Package",
            str(package.resolve()),
            "-Stage",
            str(stage.resolve()),
            "-LogPath",
            str(log_path.resolve()),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )


def exit_for_update() -> None:
    """Give the embedded window time to receive the response, then exit."""
    threading.Timer(1.5, lambda: os._exit(0)).start()
