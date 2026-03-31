@echo off
title Trading System - Backtest
color 0B
echo ============================================================
echo   BACKTEST - AGGRESSIVE MODE
echo ============================================================
echo.

cd /d C:\Users\User\Desktop\trading_system

echo Starting Docker...
docker start timescaledb >nul 2>&1
timeout /t 3 /nobreak >nul

call venv\Scripts\activate.bat

echo Running 90-day backtest with $8,000...
echo.
python scripts\backtest.py --days 90 --equity 8000

echo.
pause
