@echo off
title LETF Swing System - PCRA LIVE
color 0E
echo ============================================================
echo   LEVERAGED ETF SWING SYSTEM - PCRA LIVE
echo ============================================================
echo.
cd /d C:\Users\User\Desktop\trading_system
echo [1/2] Activating environment...
call venv\Scripts\activate
echo [2/2] Starting LETF live system...
echo.
python scripts\letf_live.py --live
echo.
echo ============================================================
echo   SESSION COMPLETE
echo ============================================================
pause
