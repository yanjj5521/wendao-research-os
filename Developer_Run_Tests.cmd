@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please run Start_Research_OS.cmd once to create .venv.
  pause
  exit /b 1
)
set "RESEARCH_OS_DATA_DIR=%TEMP%\research-os-dev-self-test-%RANDOM%"
".venv\Scripts\python.exe" self_test.py
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" self_test.py --integration
if errorlevel 1 goto :failed
set "RESEARCH_OS_DATA_DIR=%TEMP%\research-hub-dev-self-test-%RANDOM%"
".venv\Scripts\python.exe" hub_self_test.py
if errorlevel 1 goto :failed
echo.
echo All developer checks passed.
pause
exit /b 0
:failed
echo.
echo Developer checks failed. Review the message above.
pause
exit /b 1
