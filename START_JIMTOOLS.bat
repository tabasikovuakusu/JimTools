@echo off
REM ============================================================
REM JimTools - one-click start
REM Double-click this file to start the app.
REM Keep this window open while using the tool.
REM ============================================================
title JimTools
cd /d "%~dp0"
echo Starting JimTools...
echo.
echo Keep this window OPEN while using the tool.
echo Close this window to stop the app.
echo.
python app.py
echo.
echo App stopped. Press any key to close.
pause >nul
