@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo This removes only the local Python environment.
echo Your database and research files will NOT be deleted.
pause
if exist ".venv" rmdir /s /q ".venv"
echo Done. Double-click Start_Research_OS.cmd to rebuild it.
pause
