@echo off
echo Removing ScalperAutoScheduler from Windows Task Scheduler...
schtasks /delete /tn "ScalperAutoScheduler" /f
if %ERRORLEVEL% EQU 0 (
    echo [OK] Task removed. The scalper will no longer auto-start at login.
) else (
    echo [INFO] Task not found or already removed.
)
pause
