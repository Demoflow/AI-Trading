@echo off
title 0DTE Scalper
color 0D
echo ============================================================
echo   0DTE SCALPER v1.0
echo ============================================================
cd /d C:\Users\User\Desktop\trading_system
docker start timescaledb >nul 2>&1
timeout /t 5 /nobreak >nul
call venv\Scripts\activate
python scripts/scalper_live.py
pause
