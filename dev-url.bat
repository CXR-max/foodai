@echo off
title LAN Access URL
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dev-url.ps1"
echo.
pause
