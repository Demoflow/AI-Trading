@echo off
echo ============================================================
echo 0DTE SCALPER v4.0 - PAPER MODE
echo ============================================================
cd /d C:\Users\User\Desktop\trading_system
docker start timescaledb 2>nul
call venv\Scripts\activate
python scripts/scalper_live.py
pause
