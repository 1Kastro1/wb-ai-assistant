@echo off
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\autostart.ps1" -Action enable
if errorlevel 1 pause
