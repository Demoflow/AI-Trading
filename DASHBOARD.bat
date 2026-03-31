@echo off
title Trading Command Center
color 0E

cd /d C:\Users\User\Desktop\trading_system
call venv\Scripts\activate
python scripts/dashboard.py
