@echo off
setlocal
cd /d %~dp0
set WORKSPACE_ROOT=%~dp0..
set ALLOW_WRITE=0
set ALLOW_COMMANDS=0
.venv\Scripts\python.exe server.py
