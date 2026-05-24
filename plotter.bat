@echo off
title Plotter CTRL
cd /d "%~dp0"

:: Start Flask server in background
start /b "" python app.py > nul 2>&1

:: Wait for server to be ready
timeout /t 2 /nobreak > nul

:: Open browser
start http://localhost:5000

echo Plotter CTRL running at http://localhost:5000
echo Close this window to stop the server.
echo.
python app.py
