@echo off
echo ============================================================
echo VWAP STOCK SCALPER v7.0 - PAPER MODE
echo ============================================================
cd /d C:\Users\User\Desktop\trading_system
docker start timescaledb 2>nul
call venv\Scripts\activate
python scripts/scalper_live.py
pause
