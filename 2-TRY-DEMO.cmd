@echo off
rem SciVetra KB: demo on sample data. Double-click to run.
cd /d "%~dp0"
set PSModulePath=
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\try-demo.ps1" %*
echo.
pause
