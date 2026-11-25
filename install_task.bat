@echo off
setlocal
title Whimbox - Install daily update task
set "WHIMBOX_TASK_SCRIPT=%~dp0install_task.ps1"
set "WHIMBOX_ORIG_USER=%USERDOMAIN%\%USERNAME%"
set "WHIMBOX_ORIG_LOCALAPPDATA=%LOCALAPPDATA%"
powershell -NoProfile -Command "if ((New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { exit 0 } else { exit 1 }"
if errorlevel 1 (
  set "WHIMBOX_TASK_ARGS=%*"
  powershell -NoProfile -Command "$script = $env:WHIMBOX_TASK_SCRIPT.Replace('\"', '\"\"'); $user = $env:WHIMBOX_ORIG_USER.Replace('\"', '\"\"'); $local = $env:WHIMBOX_ORIG_LOCALAPPDATA.Replace('\"', '\"\"'); $argLine = '-NoProfile -ExecutionPolicy Bypass -File \"' + $script + '\" -TaskUser \"' + $user + '\" -UserLocalAppData \"' + $local + '\" -PauseOnExit'; if (-not [string]::IsNullOrWhiteSpace($env:WHIMBOX_TASK_ARGS)) { $argLine = $argLine + ' ' + $env:WHIMBOX_TASK_ARGS }; $process = Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $argLine -Wait -PassThru; exit $process.ExitCode"
  exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_task.ps1" %*
set "RC=%errorlevel%"
echo.
if not "%RC%"=="0" echo [ERROR] Scheduled task registration failed.
pause
exit /b %RC%
