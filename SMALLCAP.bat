@echo off
echo ================================================================
echo  SMALL CAP MOMENTUM TRADER — Warrior Trading Style
echo  $25,000 | Gap + Catalyst + Order Flow
echo ================================================================
cd /d C:\Users\User\Desktop\trading_system
docker start timescaledb 2>nul
call venv\Scripts\activate
python scripts/smallcap_live.py
pause
