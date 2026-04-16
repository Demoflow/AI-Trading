@echo off
title Small Cap Dashboard
echo ================================================================
echo  SMALL CAP MOMENTUM TRADER — Live Dashboard
echo  http://localhost:8889
echo ================================================================
cd /d C:\Users\User\Desktop\trading_system
call venv\Scripts\activate
python scripts/start_smallcap_dashboard.py
pause
