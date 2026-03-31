@echo off
title Trading System - LIVE TRADING (REAL MONEY!)
color 4F
echo ============================================================
echo   WARNING: LIVE TRADING - REAL MONEY
echo   THIS WILL PLACE REAL ORDERS IN YOUR SCHWAB ACCOUNT
echo ============================================================
echo.

cd /d %~dp0
cd /d C:\Users\User\Desktop\trading_system

echo [1/3] Starting Docker...
docker start timescaledb >nul 2>&1
timeout /t 3 /nobreak >nul

call venv\Scripts\activate.bat

echo.
set /p CONFIRM="Type YES to confirm LIVE trading: "
if NOT "%CONFIRM%"=="YES" (
    echo Cancelled. Use MORNING_TRADE.bat for paper mode.
    pause
    exit
)

echo.
echo Starting LIVE monitor...
python scripts\aggressive_live.py --live

pause
