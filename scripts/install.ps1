# SciVetra KB - automatic installation (Windows). Started by 1-INSTALL.cmd in the repository folder.
# What it does:
#   1. Finds Python 3.11 or newer.
#   2. Creates the working folders (default C:\SciVetra): data, backups, output, packages.
#   3. Creates a private Python virtual environment (C:\SciVetra\env); the system Python is not changed.
#   4. Links the "rkb" command to the source code in this folder (no build tools needed).
#   5. Downloads the official pypdf 6.12.1 PDF parser and checks its SHA-256 (needs Internet; optional).
#   6. Checks everything: rkb --version, rkb parse runtime-check.
# It changes no security setting and sends none of your data anywhere.
param(
    [string]$Root = "C:\SciVetra",
    [switch]$SkipPdf
)
$ErrorActionPreference = "Stop"
$PypdfSha = "8fa2a2321cf16247ed848bd7c97f193a60c08670d04abed5b0138327e51c43b0"
$PypdfFile = "pypdf-6.12.1-py3-none-any.whl"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Step($n, $text) { Write-Host ""; Write-Host "[$n/6] $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "   OK    $text" -ForegroundColor Green }
function Note($text) { Write-Host "   NOTE  $text" -ForegroundColor Yellow }
function Fail($text) {
    Write-Host ""; Write-Host "   ERROR $text" -ForegroundColor Red
    Write-Host "   See guide\08-troubleshooting.md." -ForegroundColor Red
    exit 1
}

Write-Host "=== SciVetra KB installation ===" -ForegroundColor White
Write-Host "Source code   : $Repo"
Write-Host "Working folder: $Root"
if ($Repo -match "[^\x20-\x7E]" -or $Root -match "[^\x20-\x7E]") {
    Fail "The path contains accented or special characters. Put the source code in C:\SciVetra\scivetra-kb (guide\03)."
}

Step 1 "Find Python 3.11 or newer"
$py = $null
foreach ($cand in @(@("py", "-3.14"), @("py", "-3.13"), @("py", "-3.12"), @("py", "-3.11"), @("py", "-3"), @("python"))) {
    try {
        $exe = $cand[0]; $rest = @($cand | Select-Object -Skip 1)
        $out = & $exe @rest -c "import sys; print('OK' if sys.version_info[:2] >= (3, 11) else 'OLD', sys.version.split()[0])" 2>$null
        if ($LASTEXITCODE -eq 0 -and "$out" -like "OK *") { $py = $cand; Ok ("Found Python " + ("$out".Split(" ")[1])); break }
    } catch { }
}
if (-not $py) { Fail "Python 3.11 or newer was not found. Install it (guide\01-install-python.md), then run 1-INSTALL.cmd again." }

Step 2 "Create the working folders"
foreach ($d in @("", "data", "backups", "output", "packages")) {
    $p = Join-Path $Root $d
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p | Out-Null }
}
Ok "$Root (data, backups, output, packages)"

Step 3 "Create the Python virtual environment (env)"
$venv = Join-Path $Root "env"
$vpy = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $vpy)) {
    $exe = $py[0]; $rest = @($py | Select-Object -Skip 1)
    & $exe @rest -m venv $venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $vpy)) { Fail "Could not create the virtual environment." }
    Ok "Created $venv"
} else { Ok "Already present: $venv (kept)" }

Step 4 "Link the rkb command to the source code"
$site = (& $vpy -c "import sysconfig; print(sysconfig.get_paths()['purelib'])").Trim()
[System.IO.File]::WriteAllText((Join-Path $site "scivetra-kb-src.pth"), (Join-Path $Repo "src") + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
$rkbCmd = "@echo off`r`n`"%~dp0python.exe`" -m scivetra_kb %*`r`n"
[System.IO.File]::WriteAllText((Join-Path $venv "Scripts\rkb.cmd"), $rkbCmd, (New-Object System.Text.ASCIIEncoding))
$ver = (& (Join-Path $venv "Scripts\rkb.cmd") --version).Trim()
if ($LASTEXITCODE -ne 0) { Fail "The rkb command does not run." }
Ok "rkb $ver"

Step 5 "PDF parser pypdf 6.12.1 (optional)"
$whl = Join-Path (Join-Path $Root "packages") $PypdfFile
if ($SkipPdf) {
    Note "Skipped (-SkipPdf). PDF reading (rkb parse) and part B of the demo will not work."
} else {
    if (-not (Test-Path $whl)) {
        Write-Host "   Downloading pypdf 6.12.1 from PyPI (needs Internet)..."
        & $vpy -m pip download "pypdf==6.12.1" --no-deps --only-binary=:all: -d (Join-Path $Root "packages") --disable-pip-version-check -q
    }
    if (-not (Test-Path $whl)) {
        Note "Could not download pypdf (no Internet?). Everything except PDF reading works. Run 1-INSTALL.cmd again when online."
    } else {
        $hash = (& $vpy -c "import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], 'rb').read()).hexdigest())" $whl).Trim()
        if ($hash -ne $PypdfSha) {
            Remove-Item $whl -Force
            Fail "The downloaded pypdf file is not the official release (wrong SHA-256) and was deleted. Please try again."
        }
        & $vpy -m pip install --no-index --no-deps --disable-pip-version-check -q $whl
        if ($LASTEXITCODE -ne 0) { Fail "Could not install pypdf." }
        Ok "pypdf 6.12.1 (official release, SHA-256 verified)"
    }
}

Step 6 "Final check"
$env:SCIVETRA_PYPDF_SITE = ""
$rt = & (Join-Path $venv "Scripts\rkb.cmd") parse runtime-check 2>$null
if ("$rt" -match "CONTENT_MATCHES_OFFICIAL_WHEEL") { Ok "PDF parser ready" }
elseif ("$rt" -match "PARSER_NOT_INSTALLED") { Note "No PDF parser yet (everything else works)" }
else { Note "PDF parser check: $rt" }

Write-Host ""
Write-Host "=== INSTALLATION COMPLETE ===" -ForegroundColor Green
Write-Host "Next: double-click 2-TRY-DEMO.cmd to try SciVetra on sample data."
Write-Host "Everyday work: double-click 3-OPEN-WORK-WINDOW.cmd"
