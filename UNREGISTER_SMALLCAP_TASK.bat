@echo off
echo Removing Small Cap Auto-Scheduler from Task Scheduler...
schtasks /delete /tn "SmallCapAutoScheduler" /f
if %ERRORLEVEL% EQU 0 (
    echo Done. The scheduler will no longer start automatically.
) else (
    echo Task not found or already removed.
)
pause
