@echo off
title Trading System - Full Autonomous Mode
color 0A
echo ============================================================
echo   FULL AUTONOMOUS MODE
echo   Aggressive + LETF Roth IRA (LIVE)
echo   1. Evening scan
echo   2. Wait for 9:25 AM
echo   3. Both systems trade live simultaneously
echo ============================================================
echo.

cd /d C:\Users\User\Desktop\trading_system

echo [1/3] Starting Docker...
docker start timescaledb >nul 2>&1
timeout /t 5 /nobreak >nul

call venv\Scripts\activate.bat

echo [2/3] Running full evening workflow...
python scripts\master_scheduler.py evening

echo.
echo ============================================================
echo   Evening complete. Waiting for 9:25 AM...
echo   DO NOT CLOSE THIS WINDOW
echo ============================================================
echo.

echo [3/3] Starting morning systems (Aggressive + Roth IRA)...
python scripts\master_scheduler.py full --live

echo.
echo Trading day complete.
pause
