@echo off
chcp 65001 >nul
title Whimbox Auto Update
set "PY=C:\Program Files\whimbox_app\python-embedded\python.exe"
if not exist "%PY%" (
  echo [ERROR] Embedded Python not found: %PY%
  echo Please edit PY in this .bat to match your Whimbox install path.
  pause
  exit /b 1
)
"%PY%" "%~dp0whimbox_updater.py" %*
echo.
echo ---- finished, press any key to close ----
pause >nul
