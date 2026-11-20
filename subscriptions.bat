@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "DIR=%~dp0"
:findpy_sub
if exist "%DIR%python-embedded\python.exe" (
  set "PY=%DIR%python-embedded\python.exe"
  goto :run_sub
)
set "PARENT=%DIR%..\"
for %%P in ("%DIR%.") do set "PARENT=%%~dpP"
if /i "%PARENT%"=="%DIR%" goto :run_sub
set "DIR=%PARENT%"
goto :findpy_sub

:run_sub
if not exist "%PY%" (
  echo [ERROR] 未找到奇想盒内置 Python：python-embedded\python.exe
  echo 请把本文件夹解压到奇想盒安装目录内，例如 C:\Program Files\whimbox_app\whimbox-updater
  pause
  exit /b 1
)
set "WL=%~dp0_data\watchlist.json"

if not exist "%WL%" (
  echo {"scripts": [], "note": "本文件即「我的订阅」：只自动更新这里列出的脚本。"} > "%WL%"
)

echo ============================================================
echo  打开「我的订阅」清单：在 scripts 里填脚本文件名
echo  例如 "scripts": ["家园日常.json", "朝夕心愿_采集.json"]
echo  留空 [] = 全量同步所有脚本（默认）
echo  可用文件名见 奇想盒安装目录\scripts\*.json
echo ============================================================
start "" notepad "%WL%"

echo.
echo 保存并关闭记事本后，按任意键按新清单同步一次...
pause >nul

"%PY%" "%~dp0whimbox_updater.py" --no-elevate
echo.
pause
