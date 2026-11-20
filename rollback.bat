@echo off
chcp 65001 >nul
title Whimbox - Rollback backend
set "PY=C:\Program Files\whimbox_app\python-embedded\python.exe"
"%PY%" "%~dp0whimbox_updater.py" --rollback
echo.
pause >nul
