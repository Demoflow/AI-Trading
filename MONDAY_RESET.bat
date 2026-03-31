@echo off
title Trading System - Monday Reset
color 0B

echo ============================================================
echo   MONDAY RESET - LIQUIDATE + RE-SCAN
echo   Closes old positions, scans with Elite v5.2 engine
echo ============================================================
echo.

cd /d C:\Users\User\Desktop\trading_system

echo [1/5] Starting Docker...
docker start timescaledb >nul 2>&1
timeout /t 8 /nobreak >nul
echo   Docker started.

echo [2/5] Activating environment...
call venv\Scripts\activate

echo [3/5] Liquidating old positions at market prices...
echo.
python scripts/liquidate_old.py
echo.

echo [4/5] Updating price data...
python scripts/backfill.py 2>nul
echo.

echo [5/5] Running Elite v5.2 scan for new trades...
echo.
python scripts/aggressive_scan.py
echo.

echo ============================================================
echo   RESET COMPLETE
echo   New trades loaded. Morning monitor will execute them.
echo   Leave this window open or double-click MORNING_TRADE.bat
echo ============================================================
echo.
pause
