@echo off
setlocal
chcp 65001 >nul
title Whimbox Updater

if exist "%~dp0whimbox-updater.exe" (
  "%~dp0whimbox-updater.exe" %* --pause
  exit /b
)

set "DIR=%~dp0"
set "PY="
for /l %%I in (1,1,12) do call :find_python
if not defined PY (
  echo [ERROR] Embedded Python was not found.
  echo Extract this folder inside the Whimbox installation, or download the Windows EXE release.
  pause
  exit /b 2
)
"%PY%" "%~dp0whimbox_updater.py" %* --pause
exit /b %errorlevel%

:find_python
if defined PY exit /b
if exist "%DIR%python-embedded\python.exe" (
  set "PY=%DIR%python-embedded\python.exe"
  exit /b
)
for %%P in ("%DIR%..") do set "DIR=%%~fP\"
exit /b
