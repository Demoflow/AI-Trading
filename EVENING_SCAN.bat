@echo off
title Trading System - Evening Scan
color 0B

echo ============================================================
echo   EVENING SCAN - Elite v5.3
echo ============================================================
echo.

cd /d C:\Users\User\Desktop\trading_system

echo [1/5] Starting Docker...
docker start timescaledb >nul 2>&1
timeout /t 10 /nobreak >nul
echo   Docker started.

echo [2/5] Activating environment...
call venv\Scripts\activate

echo [3/5] Syncing equity...
python scripts/sync_equity.py
echo.

echo [4/5] Updating price data...
python scripts/backfill.py 2>nul
echo.

echo [5/5] Running Elite v5.3 scan...
echo.
python scripts/aggressive_scan.py
echo.

echo [6/6] Refreshing token...
python scripts/token_keepalive.py 2>nul
echo.

echo ============================================================
echo   SCAN COMPLETE
echo   Trades saved. Morning monitor will execute at 9:00 AM.
echo ============================================================
echo.
timeout /t 10
