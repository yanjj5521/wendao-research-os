param(
    [string]$SourceExe = "",
    [switch]$NoLaunch,
    [switch]$ShortcutsOnly
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
function Get-Sha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    try {
        $sha = [Security.Cryptography.SHA256]::Create()
        try { return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace("-", "") }
        finally { $sha.Dispose() }
    } finally { $stream.Dispose() }
}

$installDir = Join-Path $env:LOCALAPPDATA "Programs\WendaoResearchV3"
$target = Join-Path $installDir "WendaoResearchV3.exe"
New-Item -ItemType Directory -Path $installDir -Force | Out-Null

if (-not $ShortcutsOnly) {
    if (-not $SourceExe) {
        $version = (Get-Content -LiteralPath (Join-Path $root "VERSION") -Raw).Trim()
        $candidates = @(
            (Join-Path $root "out\WendaoResearch-v$version-Windows-x64.exe"),
            (Join-Path $root "dist\WendaoResearchV3.exe")
        )
        $SourceExe = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    }
    if (-not $SourceExe -or -not (Test-Path -LiteralPath $SourceExe)) {
        throw "找不到 WendaoResearchV3.exe，请先运行 python tools/build.py。"
    }

    $source = (Resolve-Path -LiteralPath $SourceExe).Path
    $incoming = Join-Path $installDir ".WendaoResearchV3.install.exe"

    Get-Process -Name "WendaoResearchV3" -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $target } |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500
    Copy-Item -LiteralPath $source -Destination $incoming -Force
    $sourceHash = Get-Sha256 $incoming
    [IO.File]::Copy($incoming, $target, $true)
    $targetHash = Get-Sha256 $target
    if ($sourceHash -ne $targetHash) {
        throw "程序复制校验失败。"
    }
    Remove-Item -LiteralPath $incoming -Force -ErrorAction SilentlyContinue
    Write-Host "已安装：$target"
}

if (-not (Test-Path -LiteralPath $target)) {
    throw "尚未安装：$target。请先运行：WendaoResearchV3.exe install"
}

$shell = New-Object -ComObject WScript.Shell
$shortcuts = @(
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "问道科研.lnk"),
    (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\问道科研.lnk")
)
foreach ($shortcutPath in $shortcuts) {
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $target
    $shortcut.WorkingDirectory = $installDir
    $shortcut.IconLocation = "$target,0"
    $shortcut.Description = "问道科研 · 内置同行会"
    $shortcut.Save()
}

if ($ShortcutsOnly) {
    Write-Host "已重建快捷方式：$($shortcuts[0])"
} else {
    Write-Host "桌面图标：$($shortcuts[0])"
}
Write-Host "数据目录不会随程序安装或更新改变：$env:LOCALAPPDATA\WendaoResearchV3"
if (-not $NoLaunch -and -not $ShortcutsOnly) {
    Start-Process -FilePath $target -WorkingDirectory $installDir
}
