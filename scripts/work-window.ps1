# SciVetra KB - work window: the "rkb" command is ready and the current folder is C:\SciVetra. Started by 3-OPEN-WORK-WINDOW.cmd.
param([string]$Root = "C:\SciVetra")
$scripts = Join-Path $Root "env\Scripts"
if (-not (Test-Path (Join-Path $scripts "rkb.cmd"))) {
    Write-Host "Not installed yet. Double-click 1-INSTALL.cmd first." -ForegroundColor Red
    return
}
$env:PATH = "$scripts;$env:PATH"
$env:VIRTUAL_ENV = Join-Path $Root "env"
Set-Location $Root
function global:prompt { "(SciVetra) " + (Get-Location) + "> " }
Write-Host "=== SciVetra KB work window ===" -ForegroundColor Green
Write-Host ("rkb " + (& rkb.cmd --version))
Write-Host "Current folder: $Root   (data, backups, output)"
Write-Host "Try:  rkb doctor --data-root C:\SciVetra\data\<KB-NAME>"
Write-Host "Commands: guide\06-everyday-use.md (English) or huong-dan\06-su-dung-hang-ngay.md (Tieng Viet)"
