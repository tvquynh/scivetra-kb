@echo off
rem SciVetra KB: install (private Python environment + rkb command). Double-click to run.
cd /d "%~dp0"
set PSModulePath=
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1" %*
echo.
pause
