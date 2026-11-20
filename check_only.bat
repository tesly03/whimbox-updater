@echo off
chcp 65001 >nul
title Whimbox - Check versions only
set "PY=C:\Program Files\whimbox_app\python-embedded\python.exe"
"%PY%" "%~dp0whimbox_updater.py" --check
echo.
pause >nul
