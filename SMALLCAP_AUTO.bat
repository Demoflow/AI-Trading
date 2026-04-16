@echo off
title Small Cap Auto-Scheduler
echo ================================================================
echo  SMALL CAP AUTO-SCHEDULER
echo  Runs every trading day automatically
echo  Launch time: 6:50 AM CT (7:50 AM ET)
echo  Skips weekends + holidays
echo ================================================================
cd /d C:\Users\User\Desktop\trading_system
call venv\Scripts\activate
python scripts/smallcap_scheduler.py
pause
