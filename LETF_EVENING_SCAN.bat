@echo off
title LETF Evening Scan
color 0B
echo ============================================================
echo   LETF EVENING SCAN
echo ============================================================
echo.
cd /d C:\Users\User\Desktop\trading_system
call venv\Scripts\activate
python scripts\letf_evening_scan.py
echo.
echo ============================================================
echo   SCAN COMPLETE
echo ============================================================
timeout /t 10
