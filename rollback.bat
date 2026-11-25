@echo off
call "%~dp0run_updater.bat" --rollback %*
exit /b %errorlevel%
