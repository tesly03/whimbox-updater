@echo off
chcp 65001 >nul
title Whimbox Auto Update
setlocal

set "DIR=%~dp0"
:findpy_update
if exist "%DIR%python-embedded\python.exe" (
  set "PY=%DIR%python-embedded\python.exe"
  goto :run_update
)
set "PARENT=%DIR%..\"
for %%P in ("%DIR%.") do set "PARENT=%%~dpP"
if /i "%PARENT%"=="%DIR%" goto :run_update
set "DIR=%PARENT%"
goto :findpy_update

:run_update
if not exist "%PY%" (
  echo [ERROR] 未找到奇想盒内置 Python：python-embedded\python.exe
  echo 请把本文件夹解压到奇想盒安装目录内，例如 C:\Program Files\whimbox_app\whimbox-updater
  pause
  exit /b 1
)
"%PY%" "%~dp0whimbox_updater.py" %*
echo.
echo ---- 完成，按任意键关闭 ----
pause >nul
