@echo off
title PCRA RSI Scalper - LIVE
color 4F
echo ============================================================
echo   PCRA RSI OVERSOLD BOUNCE SCALPER
echo   Account: 16167026  Ticker: TQQQ
echo   One trade per day  /  25pct equity  /  Stop -1.5pct
echo ============================================================
echo.

cd /d C:\Users\User\Desktop\trading_system

set /p CONFIRM="Type YES to start LIVE scalper: "
if NOT "%CONFIRM%"=="YES" (
    echo Cancelled.
    pause
    exit
)

call venv\Scripts\activate.bat
echo.
echo Starting PCRA RSI Scalper (LIVE)...
python scripts\pcra_rsi_scalper.py --live

pause
