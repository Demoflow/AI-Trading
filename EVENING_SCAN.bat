@echo off
echo ============================================================
echo EVENING SCAN - Generating tomorrow's trades
echo ============================================================
cd /d C:\Users\User\Desktop\trading_system
docker start timescaledb 2>nul
call venv\Scripts\activate
python scripts/aggressive_scan.py
echo Scan complete. Trades saved to config/aggressive_trades.json
