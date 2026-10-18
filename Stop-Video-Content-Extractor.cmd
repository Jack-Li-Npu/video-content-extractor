@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Stop-Video-Content-Extractor.ps1" %*
if errorlevel 1 (
    echo.
    echo The app could not be stopped safely. See the message above.
    pause
    exit /b 1
)
exit /b 0
