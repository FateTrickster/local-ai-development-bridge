@echo off
setlocal
cd /d %~dp0
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Python virtual environment not found: .venv\Scripts\python.exe
  echo Create it first with: python -m venv .venv
  exit /b 1
)
.venv\Scripts\python.exe launcher.py start %*
