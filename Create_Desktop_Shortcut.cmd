@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "TARGET=%CD%\Start_Research_OS.cmd"
set "WORKDIR=%CD%"
set "SHORTCUT=%USERPROFILE%\Desktop\科研系统.lnk"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut('%SHORTCUT%');$s.TargetPath='%TARGET%';$s.WorkingDirectory='%WORKDIR%';$s.IconLocation='%SystemRoot%\System32\shell32.dll,13';$s.Save()"
if errorlevel 1 (
  echo Could not create the desktop shortcut.
) else (
  echo Desktop shortcut created.
)
pause
