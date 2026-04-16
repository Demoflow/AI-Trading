@echo off
:: ================================================================
:: REGISTER_SMALLCAP_TASK.bat
:: Registers the Small Cap Auto-Scheduler to start automatically
:: at Windows login. Run this ONCE as Administrator.
:: ================================================================
:: After running this, the scheduler will start every time you
:: log in to Windows. You can stop it from Task Scheduler or
:: by running UNREGISTER_SMALLCAP_TASK.bat.
:: ================================================================

echo ================================================================
echo  Registering Small Cap Auto-Scheduler with Windows Task Scheduler
echo ================================================================
echo.

:: Check for admin rights
net session >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: This script must be run as Administrator.
    echo Right-click REGISTER_SMALLCAP_TASK.bat and choose "Run as administrator"
    pause
    exit /b 1
)

set TASK_NAME=SmallCapAutoScheduler
set SCRIPT_PATH=C:\Users\User\Desktop\trading_system\SMALLCAP_AUTO.bat
set WORKING_DIR=C:\Users\User\Desktop\trading_system

:: Delete existing task if present (clean re-register)
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

:: Register: trigger at user logon, run minimized in background
schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "\"%SCRIPT_PATH%\"" ^
  /sc ONLOGON ^
  /rl HIGHEST ^
  /f ^
  /delay 0001:30

if %ERRORLEVEL% EQU 0 (
    echo.
    echo SUCCESS: Task "%TASK_NAME%" registered.
    echo.
    echo The scheduler will now start automatically each time you log in.
    echo It will launch the Small Cap Trader every trading day at 6:50 AM CT.
    echo.
    echo To start it right now without rebooting, run:
    echo   schtasks /run /tn "%TASK_NAME%"
    echo.
    echo To view it: Task Scheduler ^> Task Scheduler Library ^> %TASK_NAME%
    echo To stop it: Run UNREGISTER_SMALLCAP_TASK.bat
) else (
    echo.
    echo ERROR: Task registration failed. Make sure you ran as Administrator.
)

pause
