@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\windows\install_windows.ps1" %*
if errorlevel 1 (
  echo.
  echo 安装失败。请查看上面的错误信息。
  pause
  exit /b 1
)
endlocal
