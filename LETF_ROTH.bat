@echo off
echo ============================================================
echo LETF SWING SYSTEM - ROTH IRA
echo ============================================================
cd /d C:\Users\User\Desktop\trading_system
docker start timescaledb 2>nul
call venv\Scripts\activate
python scripts/letf_roth_live.py --live
pause
