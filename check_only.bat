@echo off
call "%~dp0run_updater.bat" --check %*
exit /b %errorlevel%
