@echo off
title Trading System - System Test
color 0B
echo ============================================================
echo   SYSTEM TEST
echo ============================================================
echo.

cd /d %~dp0
cd /d C:\Users\User\Desktop\trading_system

call venv\Scripts\activate.bat

echo [1/4] Docker...
docker start timescaledb >nul 2>&1
timeout /t 3 /nobreak >nul
docker ps --filter name=timescaledb --format "  Docker: {{.Status}}"

echo [2/4] Schwab API...
python scripts\test_schwab.py

echo [3/4] Token age...
python scripts\token_keepalive.py

echo [4/4] Pending trades...
python scripts\aggressive_status.py

echo.
echo ============================================================
echo   TEST COMPLETE
echo ============================================================
pause
