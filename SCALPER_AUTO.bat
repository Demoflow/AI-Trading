@echo off
title Scalper Auto-Scheduler
echo ================================================================
echo  0DTE SCALPER AUTO-SCHEDULER
echo  Launches scalper_live.py every trading day at 8:15 AM CT
echo  Skips weekends and holidays
echo ================================================================
cd /d C:\Users\User\Desktop\trading_system
call venv\Scripts\activate
python scripts/scalper_scheduler.py
pause
