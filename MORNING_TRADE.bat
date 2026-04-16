@echo off
title Trading System - Morning Live (Elite + Roth LETF + PCRA LETF)
color 0A
echo ============================================================
echo   MORNING LIVE TRADING
echo   Elite Options (Brokerage) + LETF Roth IRA + LETF PCRA
echo ============================================================
cd /d C:\Users\User\Desktop\trading_system
docker start timescaledb 2>nul
timeout /t 3 /nobreak >nul
call venv\Scripts\activate
python scripts\master_scheduler.py morning --live
pause
