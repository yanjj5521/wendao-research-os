@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title 科研系统 Shared Hub
rem 需要强制 HTTPS 时：set HUB_HTTPS_ONLY=1 && set HUB_TRUST_PROXY=1 后再运行本脚本。

set "PY_CMD="
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys; assert sys.version_info >= (3, 11)" >nul 2>nul
  if not errorlevel 1 set "PY_CMD=py -3"
)
if not defined PY_CMD (
  where python >nul 2>nul
  if not errorlevel 1 (
    python -c "import sys; assert sys.version_info >= (3, 11)" >nul 2>nul
    if not errorlevel 1 set "PY_CMD=python"
  )
)
if not defined PY_CMD (
  echo Python 3.11 or newer was not found.
  echo Install Python and enable Add Python to PATH.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Creating the local environment...
  %PY_CMD% -m venv .venv
  if errorlevel 1 goto :failed
)
set "LOCAL_PY=.venv\Scripts\python.exe"
%LOCAL_PY% -c "import fastapi,uvicorn,jinja2,bleach" >nul 2>nul
if errorlevel 1 (
  echo [2/3] Installing required packages...
  %LOCAL_PY% -m pip install --disable-pip-version-check --upgrade pip
  %LOCAL_PY% -m pip install --disable-pip-version-check -r requirements.txt
  if errorlevel 1 goto :failed
) else (
  echo [2/3] Local packages are ready.
)
echo [3/3] Starting the Shared Hub on port 5050...
%LOCAL_PY% run_hub.py
goto :end
:failed
echo Shared Hub setup failed. Review the error above.
pause
exit /b 1
:end
endlocal

rem 管理员首次凭据保存在 instance\HUB_ADMIN_CREDENTIALS.txt，登录成功修改密码后自动销毁。
