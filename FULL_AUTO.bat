@echo off
title Trading System - Full Autonomous Mode
color 0A
echo ============================================================
echo   FULL AUTONOMOUS MODE
echo   This will:
echo     1. Run evening scan now
echo     2. Wait until 9:25 AM CT tomorrow
echo     3. Start morning trading automatically
echo ============================================================
echo.

cd /d %~dp0
cd /d C:\Users\User\Desktop\trading_system

echo [1/5] Starting Docker...
docker start timescaledb >nul 2>&1
timeout /t 5 /nobreak >nul

call venv\Scripts\activate.bat

echo [2/5] Updating price data...
python scripts\backfill.py

echo [3/5] Running evening scan...
python scripts\aggressive_scan.py

echo [4/5] Token keepalive...
python scripts\token_keepalive.py

echo.
echo ============================================================
echo   Evening scan complete. Waiting for market open...
echo   DO NOT CLOSE THIS WINDOW
echo   Trading will start automatically at 9:25 AM CT
echo ============================================================
echo.

python -c "import time; from datetime import datetime; target_h=9; target_m=25; now=datetime.now(); print(f'Current time: {now.strftime("%%H:%%M")}'); [None for _ in iter(lambda: (time.sleep(30), datetime.now().hour*60+datetime.now().minute >= target_h*60+target_m)[-1], True)] if now.hour*60+now.minute < target_h*60+target_m or now.hour >= 16 else None; print('Market time! Starting monitor...')"

echo [5/5] Starting morning monitor...
python scripts\aggressive_live.py

echo.
echo Trading day complete.
pause
