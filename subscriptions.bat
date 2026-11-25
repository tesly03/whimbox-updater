@echo off
call "%~dp0run_updater.bat" --edit-subscriptions %*
exit /b %errorlevel%
