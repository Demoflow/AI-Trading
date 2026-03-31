@echo off
title LETF Roth IRA - Dad's Account
color 0D
echo ============================================================
echo   LEVERAGED ETF SWING - DAD'S ROTH IRA
echo ============================================================
echo.
cd /d C:\Users\User\Desktop\trading_system
call venv\Scripts\activate
python scripts\letf_roth_live.py --live
echo.
echo ============================================================
echo   SESSION COMPLETE
echo ============================================================
pause
