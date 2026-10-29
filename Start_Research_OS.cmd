@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title 科研系统

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
  echo Python 3 was not found.
  echo Install Python 3.11 or newer and enable "Add Python to PATH".
  echo Then double-click this file again.
  pause
  exit /b 1
)

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys; assert sys.version_info >= (3, 11)" >nul 2>nul
  if errorlevel 1 rmdir /s /q ".venv"
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Creating the local environment...
  %PY_CMD% -m venv .venv
  if errorlevel 1 goto :failed
)

set "LOCAL_PY=.venv\Scripts\python.exe"
%LOCAL_PY% -c "import fastapi,uvicorn,jinja2,pypdf,docx,pptx,openpyxl,PIL,bleach,webview" >nul 2>nul
if errorlevel 1 (
  echo [2/3] Installing required packages for the first launch...
  %LOCAL_PY% -m pip install --disable-pip-version-check --upgrade pip
  %LOCAL_PY% -m pip install --disable-pip-version-check -r requirements.txt
  if errorlevel 1 goto :failed
) else (
  echo [2/3] Local packages are ready.
)

echo [3/3] Starting the desktop application...
%LOCAL_PY% run_local.py
goto :end

:failed
echo.
echo Startup setup failed. The error above is the useful diagnostic.
echo You can also run: py -3 run_local.py
pause
exit /b 1

:end
endlocal
