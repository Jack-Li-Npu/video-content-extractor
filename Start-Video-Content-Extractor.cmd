@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Video-Content-Extractor.ps1" -OpenBrowser %*
if errorlevel 1 (
    echo.
    echo The app did not start. See the message above.
    pause
    exit /b 1
)
exit /b 0
