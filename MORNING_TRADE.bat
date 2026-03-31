@echo off
title Trading System - Morning Trade
color 0A

echo ============================================================
echo   MORNING TRADE - Elite v5.3
echo ============================================================
echo.

cd /d C:\Users\User\Desktop\trading_system

echo [1/3] Starting Docker...
docker start timescaledb >nul 2>&1
timeout /t 10 /nobreak >nul
echo   Docker started.

echo [2/3] Activating environment...
call venv\Scripts\activate

echo [3/3] Liquidate underperformers...
python scripts\liquidate_underperformers.py
timeout /t 5 /nobreak >nul
echo [4/4] Starting trade monitor...
echo   (Auto-detects stale trades and rescans if needed)
echo.
python scripts/aggressive_live.py --live
echo.

echo ============================================================
echo   MARKET CLOSED - Session complete.
echo ============================================================
echo.
pause
