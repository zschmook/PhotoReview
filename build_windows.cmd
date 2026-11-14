@echo off
cd /d "%~dp0"
.venv\Scripts\python.exe tools\build_windows.py %*
exit /b %errorlevel%
