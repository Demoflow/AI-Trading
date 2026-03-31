@echo off
title Trading System - Status
color 0B

cd /d %~dp0
cd /d C:\Users\User\Desktop\trading_system

call venv\Scripts\activate.bat
python scripts\aggressive_status.py

echo.
pause
