@echo off
title Scalper Status
color 0D
cd /d C:\Users\User\Desktop\trading_system
call venv\Scripts\activate
python scripts/scalper_status.py
pause
