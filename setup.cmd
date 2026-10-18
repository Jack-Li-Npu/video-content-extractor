@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
if errorlevel 1 (
    echo.
    echo Setup failed. See the message above.
    pause
    exit /b 1
)
exit /b 0
