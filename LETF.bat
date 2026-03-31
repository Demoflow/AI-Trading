@echo off
title LETF Swing System
color 0E
echo ============================================================
echo   LEVERAGED ETF SWING SYSTEM - PCRA
echo ============================================================
echo.
cd /d C:\Users\User\Desktop\trading_system
echo [1/2] Activating environment...
call venv\Scripts\activate
echo [2/2] Starting LETF system...
echo.
python scripts\letf_live.py
echo.
echo ============================================================
echo   SESSION COMPLETE
echo ============================================================
pause
