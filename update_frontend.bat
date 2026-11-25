@echo off
call "%~dp0run_updater.bat" --install-frontend --no-backend --no-scripts %*
exit /b %errorlevel%
