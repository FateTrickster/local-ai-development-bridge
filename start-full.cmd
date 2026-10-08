@echo off
setlocal
cd /d %~dp0
set WORKSPACE_ROOT=%~dp0..
set ALLOW_WRITE=1
set ALLOW_COMMANDS=1
.venv\Scripts\python.exe server.py
