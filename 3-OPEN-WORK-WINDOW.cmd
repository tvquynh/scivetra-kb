@echo off
rem SciVetra KB: open a PowerShell window where the rkb command is ready. Double-click to run.
set PSModulePath=
powershell -NoExit -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\work-window.ps1" %*
