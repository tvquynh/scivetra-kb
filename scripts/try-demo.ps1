# SciVetra KB - demo on SAMPLE data (generated, not real). Started by 2-TRY-DEMO.cmd.
# Part A (about 1 minute): create a practice knowledge base (KB), check its health, back it up and verify the backup.
# Part B (5-10 minutes, needs the PDF parser): generate 32 simulated papers, run the whole pipeline, export a
#   citation-audited synthesis draft and open it in Notepad.
param(
    [string]$Root = "C:\SciVetra",
    [switch]$PartAOnly
)
$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venv = Join-Path $Root "env"
$vpy = Join-Path $venv "Scripts\python.exe"
$rkb = Join-Path $venv "Scripts\rkb.cmd"
$whl = Join-Path $Root "packages\pypdf-6.12.1-py3-none-any.whl"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"

function Fail($text) {
    Write-Host ""; Write-Host "   ERROR $text" -ForegroundColor Red
    Write-Host "   See guide\08-troubleshooting.md." -ForegroundColor Red
    exit 1
}
function Rkb {
    $out = & $rkb @args 2>$null
    $json = $null
    try { $json = "$out" | ConvertFrom-Json } catch { }
    return $json
}
if (-not (Test-Path $rkb)) { Fail "Not installed yet. Double-click 1-INSTALL.cmd first." }

Write-Host "=== SciVetra KB demo (sample data) ===" -ForegroundColor White
Write-Host ""
Write-Host "PART A - Practice knowledge base" -ForegroundColor Cyan
$kb = Join-Path $Root "data\demo-$stamp"
$r = Rkb init --data-root $kb
if (-not $r -or -not $r.initialized) { Fail "Could not create the knowledge base: $r" }
Write-Host "   OK    Created knowledge base: $kb" -ForegroundColor Green
$r = Rkb doctor --data-root $kb --full
if (-not $r.ok) { Fail "The health check reported a problem." }
Write-Host "   OK    Health check: no problems" -ForegroundColor Green
$r = Rkb backup --data-root $kb --target (Join-Path $Root "backups")
if (-not $r.ok) { Fail "Backup failed." }
$bk = $r.backup_dir
Write-Host "   OK    Backed up to: $bk" -ForegroundColor Green
$r = Rkb verify-backup --backup $bk
if (-not $r.valid) { Fail "The backup is not valid." }
Write-Host "   OK    Backup verified: valid" -ForegroundColor Green

if ($PartAOnly) { Write-Host ""; Write-Host "Part A done." -ForegroundColor Green; exit 0 }
if (-not (Test-Path $whl)) {
    Write-Host ""
    Write-Host "Skipping part B: the PDF parser is not installed. Run 1-INSTALL.cmd again when online." -ForegroundColor Yellow
    exit 0
}

Write-Host ""
Write-Host "PART B - Synthesis draft from 32 simulated papers (5-10 minutes, please wait...)" -ForegroundColor Cyan
$out = Join-Path $Root "output\demo-draft-$stamp"
$env:SCIVETRA_PYPDF_WHEEL = $whl
$env:SCIVETRA_PYPDF_SITE = $whl
$log = Join-Path $Root "output\demo-draft-$stamp.log"
$evalArgs = '-I -B "{0}" --out-dir "{1}"' -f (Join-Path $Repo "tools\s11_evaluation.py"), $out
$proc = Start-Process -FilePath $vpy -ArgumentList $evalArgs -NoNewWindow -Wait -PassThru -RedirectStandardOutput $log -RedirectStandardError "$log.err"
$code = $proc.ExitCode
$sumFile = Join-Path $out "s11_evaluation_summary.json"
if ($code -ne 0 -or -not (Test-Path $sumFile)) { Fail "Part B did not complete. Log: $log" }
$s = Get-Content $sumFile -Raw -Encoding UTF8 | ConvertFrom-Json
Write-Host ("   OK    {0} claims from {1} simulated papers" -f $s.claims, $s.studies) -ForegroundColor Green
Write-Host ("   OK    Agreement with the answer key: {0:P0}; wrongly supported claims: {1}; citation traceability: {2}" -f $s.metrics.agreement, $s.metrics.false_supported, $s.traceability) -ForegroundColor Green
$draft = Get-ChildItem -Path (Join-Path $out "run1\audited_example_draft") -Recurse -Filter "draft.md" | Select-Object -First 1
if ($draft) {
    Write-Host "   OK    Draft: $($draft.FullName)" -ForegroundColor Green
    Start-Process notepad.exe $draft.FullName
}
Write-Host ""
Write-Host "=== DEMO COMPLETE ===" -ForegroundColor Green
Write-Host "Results: $out"
Write-Host "What the results mean: guide\05-demo.md"
