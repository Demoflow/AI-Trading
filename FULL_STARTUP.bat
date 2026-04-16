@echo off
echo ============================================================
echo PAPER-ONLY STARTUP - SCALPER + DASHBOARD
echo ============================================================
echo.
echo LIVE ACCOUNTS DISABLED:
echo   - Elite v7 (brokerage)  OFF
echo   - LETF Roth             OFF
echo.
echo RUNNING:
echo   - 0DTE Scalper (paper)
echo   - Dashboard
echo ============================================================
cd /d C:\Users\User\Desktop\trading_system
docker start timescaledb 2>nul

echo Starting 0DTE Scalper (paper)...
start "Scalper" cmd /k "cd /d C:\Users\User\Desktop\trading_system && call venv\Scripts\activate && python scripts/scalper_live.py"

timeout /t 3 /nobreak >nul

echo Starting Scalper Dashboard...
start "Dashboard" cmd /k "cd /d C:\Users\User\Desktop\trading_system && call venv\Scripts\activate && python scripts/start_dashboard.py"

timeout /t 3 /nobreak >nul

echo Opening dashboard in browser...
start http://localhost:8888

echo ============================================================
echo Paper systems launched.
echo Dashboard: http://localhost:8888
echo ============================================================
