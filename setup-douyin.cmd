@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-douyin.ps1" %*
if errorlevel 1 (
    echo.
    echo Optional speech setup failed. See the message above.
    pause
    exit /b 1
)
exit /b 0
