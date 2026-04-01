@echo off
echo ============================================================
echo ELITE v7 - AUTONOMOUS LIVE TRADING
echo ============================================================
cd /d C:\Users\User\Desktop\trading_system
docker start timescaledb 2>nul
call venv\Scripts\activate
python scripts/aggressive_live.py --live
pause
