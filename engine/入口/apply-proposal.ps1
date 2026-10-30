param([switch]$DryRun)
# Apply a pending proposal into the protected persona folder.
# ASCII-only on purpose: PS 5.1 reads .ps1 as ANSI(GBK), so Chinese literals would be mangled.

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here

# locate spec_apply.py without hardcoding Chinese dir names
$script = Get-ChildItem -Path $root -Recurse -Filter "spec_apply.py" -ErrorAction SilentlyContinue |
          Select-Object -First 1
if (-not $script) {
    Write-Host "Cannot find spec_apply.py under $root" -ForegroundColor Red
    if (-not $DryRun) { Read-Host "Press Enter to close" }
    exit 1
}
$scriptPath = $script.FullName

# resolve python: env -> known runtime -> any runtime -> PATH
$py = $null
if ($env:DSH_PY -and (Test-Path $env:DSH_PY)) { $py = $env:DSH_PY }
if (-not $py) {
    $known = Join-Path $env:USERPROFILE ".dsh\dsh-runtimes\dsh-next-primary-runtime\dependencies\python\python.exe"
    if (Test-Path $known) { $py = $known }
}
if (-not $py) {
    $hit = Get-ChildItem (Join-Path $env:USERPROFILE ".dsh\dsh-runtimes") -Recurse -Filter "python.exe" -ErrorAction SilentlyContinue |
           Select-Object -First 1
    if ($hit) { $py = $hit.FullName }
}
if (-not $py) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $py = $cmd.Source }
}
if (-not $py) {
    Write-Host "Cannot find python.exe. Set DSH_PY and retry." -ForegroundColor Red
    if (-not $DryRun) { Read-Host "Press Enter to close" }
    exit 1
}

Write-Host "python : $py"
Write-Host "script : $scriptPath"

if ($DryRun) {
    Write-Host ""
    Write-Host "[DryRun] proposals:" -ForegroundColor Cyan
    & $py $scriptPath list
    exit 0
}

Write-Host ""
Write-Host "Proposals:" -ForegroundColor Cyan
& $py $scriptPath list

Write-Host ""
$id = Read-Host "Proposal id to accept (empty = cancel)"
if ([string]::IsNullOrWhiteSpace($id)) {
    Write-Host "Cancelled."
    Read-Host "Press Enter to close"
    exit 0
}

& $py $scriptPath accept --id $id

Write-Host ""
Write-Host "Done. If you see [OK] preflight above, nothing broke." -ForegroundColor Yellow
Read-Host "Press Enter to close"
