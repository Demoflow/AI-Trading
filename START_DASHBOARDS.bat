@echo off
title Trading Dashboards
echo ================================================================
echo  TRADING DASHBOARDS — Starting both servers
echo ================================================================
echo  Scalper Dashboard:   http://172.16.101.48:8888
echo  Small Cap Dashboard: http://172.16.101.48:8889
echo.
echo  Use these URLs on your phone (must be on same Wi-Fi network).
echo  Close this window to stop both servers.
echo ================================================================

cd /d C:\Users\User\Desktop\trading_system
call venv\Scripts\activate

:: Start scalper dashboard in a new window
start "Scalper Dashboard (port 8888)" cmd /k "cd /d C:\Users\User\Desktop\trading_system && call venv\Scripts\activate && python scripts\start_dashboard.py"

:: Start small cap dashboard in a new window
start "Small Cap Dashboard (port 8889)" cmd /k "cd /d C:\Users\User\Desktop\trading_system && call venv\Scripts\activate && python scripts\start_smallcap_dashboard.py"

echo.
echo Both dashboards launched in separate windows.
echo Press any key to exit this window (servers keep running).
pause
