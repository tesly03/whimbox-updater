@echo off
setlocal
title Whimbox - Install daily update task
set "WHIMBOX_TASK_INSTALLER=%~f0"
powershell -NoProfile -Command "if ((New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { exit 0 } else { exit 1 }"
if errorlevel 1 (
  powershell -NoProfile -Command "$process = Start-Process -FilePath $env:WHIMBOX_TASK_INSTALLER -Verb RunAs -Wait -PassThru; exit $process.ExitCode"
  exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_task.ps1" %*
set "RC=%errorlevel%"
echo.
if not "%RC%"=="0" echo [ERROR] Scheduled task registration failed.
pause
exit /b %RC%
