@echo off
title Trading System - Token Refresh
color 0D
echo ============================================================
echo   SCHWAB TOKEN REFRESH
echo   A browser will open. Log in and authorize.
echo ============================================================
echo.

cd /d %~dp0
cd /d C:\Users\User\Desktop\trading_system

call venv\Scripts\activate.bat
python scripts\authenticate_schwab.py

echo.
echo Token refreshed!
pause
