@echo off
echo ============================================================
echo FULL SYSTEM STARTUP - ALL TRADING SYSTEMS
echo ============================================================
cd /d C:\Users\User\Desktop\trading_system
docker start timescaledb 2>nul

echo Starting Elite v7 (Brokerage)...
start "Elite_v7" cmd /k "cd /d C:\Users\User\Desktop\trading_system && call venv\Scripts\activate && python scripts/aggressive_live.py --live"

timeout /t 5 /nobreak >nul

echo Starting LETF Roth...
start "LETF_Roth" cmd /k "cd /d C:\Users\User\Desktop\trading_system && call venv\Scripts\activate && python scripts/letf_roth_live.py --live"

timeout /t 5 /nobreak >nul

echo Starting 0DTE Scalper (paper)...
start "Scalper" cmd /k "cd /d C:\Users\User\Desktop\trading_system && call venv\Scripts\activate && python scripts/scalper_live.py"

echo ============================================================
echo All systems launched in separate windows.
echo ============================================================
