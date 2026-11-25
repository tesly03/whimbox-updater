@echo off
call "%~dp0run_updater.bat" --install-frontend %*
exit /b %errorlevel%
