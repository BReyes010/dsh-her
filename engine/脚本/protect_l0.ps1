# protect_l0.ps1 - lock the "人格" folder so only Administrators can write
#
# Run as Administrator:
#   powershell -ExecutionPolicy Bypass -File .\脚本\protect_l0.ps1
# Undo (restore inheritance, everyone writable again):
#   powershell -ExecutionPolicy Bypass -File .\脚本\protect_l0.ps1 -Revert
#
# Uses well-known SIDs (not localized names) so it works on zh-CN Windows:
#   *S-1-5-32-544 = Administrators
#   *S-1-5-32-545 = Users
#   *S-1-5-18     = SYSTEM

param([switch]$Revert)

$ErrorActionPreference = "Stop"
$target = Join-Path (Split-Path -Parent $PSScriptRoot) "人格"
$card = Join-Path $target "人格卡.md"

$me = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = (New-Object Security.Principal.WindowsPrincipal($me)).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "[X] Not elevated. Right-click PowerShell -> Run as administrator." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $target)) {
    Write-Host "[X] Folder not found: $target" -ForegroundColor Red
    exit 1
}
Write-Host "Target: $target"

if ($Revert) {
    Write-Host "-> restore inheritance (writable again)"
    & icacls $target /inheritance:e
    Write-Host "[OK] Reverted." -ForegroundColor Yellow
    exit 0
}

# 1) reset ACL: Admin/SYSTEM full, Users read+execute (SIDs, not names)
Write-Host "-> resetting ACL (SIDs: Administrators / SYSTEM full, Users RX)"
& icacls $target /inheritance:r /grant:r `
    "*S-1-5-32-544:(OI)(CI)F" `
    "*S-1-5-18:(OI)(CI)F" `
    "*S-1-5-32-545:(OI)(CI)RX" `
    /T /C
if ($LASTEXITCODE -ne 0) {
    Write-Host "[X] icacls failed with exit code $LASTEXITCODE (see output above)." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "=== folder ACL ==="
& icacls $target
Write-Host "=== card ACL ==="
& icacls $card

# 2) read test must pass
$readOk = $true
try { Get-Content $card -TotalCount 1 -ErrorAction Stop | Out-Null } catch { $readOk = $false }
if ($readOk) {
    Write-Host "[OK] Read test passed (card is readable)" -ForegroundColor Green
} else {
    Write-Host "[X] Read test FAILED - files are not readable by Users. Run -Revert and re-apply." -ForegroundColor Red
    exit 1
}

# 3) write test
$probe = Join-Path $target ".write-test"
try {
    Set-Content -Path $probe -Value "x" -ErrorAction Stop
    Remove-Item $probe -Force -ErrorAction SilentlyContinue
    Write-Host "[!] Write succeeded in THIS shell (it is elevated)." -ForegroundColor Yellow
    Write-Host "    Verify from a NORMAL window: it must fail, and l0_guard.py must print 'physical protection ON'."
} catch {
    Write-Host "[OK] Write denied even here - protection is strong." -ForegroundColor Green
}

Write-Host ""
Write-Host "Notes:"
Write-Host "  - l0_apply.py --accept must run in an elevated window"
Write-Host "  - backups: 备份\L0 (outside the locked folder)"
Write-Host "  - revert: .\脚本\protect_l0.ps1 -Revert"
