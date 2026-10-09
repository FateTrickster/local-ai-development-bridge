@echo off
setlocal
cd /d %~dp0
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Python virtual environment not found: .venv\Scripts\python.exe
  exit /b 1
)
.venv\Scripts\python.exe launcher.py status
